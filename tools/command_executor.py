import subprocess
import shlex
from typing import Tuple, Optional


class CommandExecutor:
    def execute_command(
        self, command: str, timeout: int = 60, input: Optional[str] = None
    ) -> Tuple[int, str, str]:
        """
        Executes a command and returns the exit code, stdout, and stderr.
        """
        try:
            cmd_list = shlex.split(command)
            result = subprocess.run(
                cmd_list,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                input=input,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as e:
            return -1, str(e), "TimeoutExpired"
        except Exception as e:
            return -1, str(e), "Exception"
