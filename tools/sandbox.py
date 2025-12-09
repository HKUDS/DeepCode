
import os
import asyncio
import base64
import tarfile
import io
from typing import Optional, Dict, Any
from microsandbox import PythonSandbox

class Sandbox:
    def __init__(self, server_url: str = "http://172.19.48.230:5555"):
        self.server_url = os.environ.get("MICROSANDBOX_URL", server_url)
        self.sandbox: Optional[PythonSandbox] = None
        self._connected = False

    async def start_with_mount(self, volumes: list[str]):
        """
        Start the sandbox with volume mounts using raw API.
        """
        import aiohttp
        import json
        
        # We need to get the sandbox ID or name from the object if possible,
        # or create a new one.
        # The SDK create() makes a new object but doesn't start it yet.
        # We need to know the sandbox name/ID that the SDK object is using.
        # Inspecting the SDK object might reveal it.
        
        if not self.sandbox:
             # Initialize the SDK object first to get a handle
             self.cm = PythonSandbox.create(server_url=self.server_url)
             self.sandbox = await self.cm.__aenter__()
             self._connected = True
             
        # Now we try to start it with volumes via API.
        # We need the sandbox name.
        # Let's assume self.sandbox.name or similar exists.
        # Based on verify_sandbox.py, we don't see a name property usage.
        # But the API requires a sandbox name.
        
        # If we can't get the name, we might have to create our own name and pass it to create()
        # if create() accepts a name.
        # inspect_sandbox_instance.py showed: 
        # (server_url: str = None, namespace: str = 'default', sandbox_name: Optional[str] = None, ...)
        
        # So we can pass a name!
        
        # But wait, we already created it in connect().
        # We should modify connect() to accept volumes or use a separate method that reconnects.
        pass

    async def connect(self, volumes: list[str] = None):
        if self._connected and self.sandbox:
            return

        try:
            # Generate a random name if not provided, so we can refer to it
            import uuid
            sandbox_name = f"sb-{uuid.uuid4().hex[:8]}"
            
            self.cm = PythonSandbox.create(server_url=self.server_url, sandbox_name=sandbox_name)
            self.sandbox = await self.cm.__aenter__()
            self._connected = True
            
            if volumes:
                # Use raw API to start with volumes
                import aiohttp
                
                # Construct API URL
                # self.server_url might be base URL. API is at /api/v1/rpc
                api_url = f"{self.server_url.rstrip('/')}/api/v1/rpc"
                
                payload = {
                    "jsonrpc": "2.0",
                    "method": "sandbox.start",
                    "params": {
                        "sandbox": sandbox_name,
                        "namespace": "default",
                        "config": {
                            "image": "microsandbox/python", # Default image
                            "memory": 512,
                            "workdir": "/workspace",
                            "volumes": volumes,
                            "timeout": 180
                        }
                    },
                    "id": "start-1"
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(api_url, json=payload) as resp:
                        if resp.status != 200:
                             raise Exception(f"Failed to start sandbox via API: {await resp.text()}")
                        data = await resp.json()
                        if "error" in data:
                             raise Exception(f"API error: {data['error']}")
                             
                # We don't call self.sandbox.start() because we just started it via API.
                # The SDK object should be able to attach to it or just run commands
                # since we initialized it with the same name.
                
            else:
                # Start the sandbox container using SDK default
                await self.sandbox.start()
            
        except Exception as e:
            print(f"Failed to connect to sandbox: {e}")
            raise

    async def close(self):
        if self.cm:
            await self.cm.__aexit__(None, None, None)
            self._connected = False
            self.sandbox = None

    async def run_code(self, code: str) -> Dict[str, Any]:
        if not self._connected or not self.sandbox:
            await self.connect()

        try:
            exec_result = await self.sandbox.run(code)
            output = await exec_result.output()
            
            # The SDK might return an object with output() method or just the result.
            # verify_sandbox.py says: exec = await sb.run(...); await exec.output()
            
            return {
                "status": "success",
                "output": output,
                "error": None # We might need to parse error from output or result
            }
        except Exception as e:
            return {
                "status": "error",
                "output": "",
                "error": str(e)
            }

    async def run_shell_command(self, command: str) -> Dict[str, Any]:
        """
        Execute a shell command in the sandbox.
        """
        if not self._connected or not self.sandbox:
            await self.connect()

        # Since PythonSandbox might not expose command execution directly,
        # we wrap it in a Python script using subprocess.
        # This is a reliable fallback.
        
        # Escape the command for python string
        safe_command = command.replace("\\", "\\\\").replace("'", "\\'")
        
        wrapper_code = f"""
import subprocess
import sys

try:
    result = subprocess.run('{safe_command}', shell=True, capture_output=True, text=True)
    print(result.stdout, end='')
    print(result.stderr, file=sys.stderr, end='')
except Exception as e:
    print(f"Error executing command: {{e}}", file=sys.stderr)
"""
        return await self.run_code(wrapper_code)

    async def upload_project(self, local_path: str, remote_path: str = "/workspace"):
        """
        Upload a local directory to the sandbox.
        """
        if not self._connected or not self.sandbox:
            await self.connect()

        # 1. Create tarball in memory
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w:gz") as tar:
            tar.add(local_path, arcname=".")
        
        tar_bytes = bio.getvalue()
        b64_tar = base64.b64encode(tar_bytes).decode('utf-8')
        
        # 2. Upload and extract
        # We write the base64 string to a file, decode it, and untar it.
        # We do this in chunks if necessary, but for now let's try one go.
        
        setup_script = f"""
import base64
import os
import subprocess
import sys

try:
    os.makedirs('{remote_path}', exist_ok=True)
    os.chdir('{remote_path}')
    
    b64_data = "{b64_tar}"
    with open('project.tar.gz', 'wb') as f:
        f.write(base64.b64decode(b64_data))
        
    subprocess.run(['tar', '-xzf', 'project.tar.gz'], check=True)
    os.remove('project.tar.gz')
    print("Upload successful")
except Exception as e:
    print(f"Upload failed: {{e}}")
    sys.exit(1)
"""
        result = await self.run_code(setup_script)
        if "Upload successful" not in result["output"]:
            raise Exception(f"Upload failed: {result['output']} {result.get('error', '')}")

    async def compile_project(self, project_path: str) -> Dict[str, Any]:
        """
        Upload and compile/check the project.
        """
        try:
            # Upload
            await self.upload_project(project_path)
            
            # Compile (Check for syntax errors)
            # We can also run pip install if requirements.txt exists
            
            compile_script = """
import subprocess
import os
import sys

def run_command(cmd):
    print(f"Running: {cmd}")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print(f"Error output: {res.stderr}")
    return res.returncode

os.chdir('/workspace')

# 1. Install dependencies if requirements.txt exists
if os.path.exists('requirements.txt'):
    print("Installing dependencies...")
    # Note: This might take time and require internet in the sandbox
    # We skip it for now or make it optional? 
    # The user asked for "compilation", which usually implies checking the code.
    # Installing deps is "setup".
    # Let's try to just compileall first to check syntax.
    pass

# 2. Compile all python files
print("Compiling...")
ret = run_command('python -m compileall .')

if ret == 0:
    print("Compilation successful")
else:
    print("Compilation failed")
    sys.exit(1)
"""
            return await self.run_code(compile_script)
            
        except Exception as e:
            return {
                "status": "error",
                "output": "",
                "error": str(e)
            }
