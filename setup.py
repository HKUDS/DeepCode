import re
from pathlib import Path

import setuptools

ROOT = Path(__file__).resolve().parent


# Reading the long description from README.md
def read_long_description():
    try:
        return (ROOT / "README.md").read_text(encoding="utf-8")
    except FileNotFoundError:
        return "DeepCode: an open agentic coding system."


# Read the canonical product version without importing runtime dependencies.
def read_version():
    source = (ROOT / "core" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if match is None:
        raise RuntimeError("core/version.py does not define __version__")
    return match.group(1)


# Reading dependencies from requirements.txt
def read_requirements():
    deps = []
    try:
        with (ROOT / "requirements.txt").open(encoding="utf-8") as f:
            deps = [
                line.strip() for line in f if line.strip() and not line.startswith("#")
            ]
    except FileNotFoundError:
        print(
            "Warning: 'requirements.txt' not found. No dependencies will be installed."
        )
    return deps


long_description = read_long_description()
requirements = read_requirements()

setuptools.setup(
    name="deepcode-hku",
    url="https://github.com/HKUDS/DeepCode",
    version=read_version(),
    author="DeepCode Team",
    license="MIT",
    description="Open agentic coding with durable sessions, goals, and verification",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(
        exclude=("tests*", "docs*", ".history*", ".git*", ".ruff_cache*")
    ),
    py_modules=["deepcode"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Operating System :: OS Independent",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Text Processing :: Linguistic",
    ],
    python_requires=">=3.12",
    install_requires=requirements,
    extras_require={
        "advanced-documents": ["docling>=2.113.0"],
        "test": [
            "pytest>=8,<10",
            "pytest-asyncio>=1,<2",
        ],
    },
    include_package_data=True,
    package_data={
        "core.application.goal_prompts": ["*.md"],
        "tools": ["*.yaml"],
    },
    entry_points={
        "console_scripts": [
            "deepcode=deepcode:main",
            "deepcode-app-server=app_server.__main__:main",
        ],
    },
    project_urls={
        "Documentation": "https://github.com/HKUDS/DeepCode#readme",
        "Source": "https://github.com/HKUDS/DeepCode",
        "Tracker": "https://github.com/HKUDS/DeepCode/issues",
    },
)
