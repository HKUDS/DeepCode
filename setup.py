import re
from pathlib import Path

import setuptools

ROOT = Path(__file__).resolve().parent


REPOSITORY = "https://github.com/HKUDS/DeepCode"
# Markdown links whose target is relative: ](docs/x.md), ](#anchor), ](LICENSE).
_RELATIVE_LINK = re.compile(r"\]\((?![a-z][a-z0-9+.-]*:|//)([^)\s]+)\)", re.IGNORECASE)


def _absolute_links(markdown):
    """PyPI renders the README outside the repository, where relative links
    break. Point them at the repository on GitHub instead."""

    def absolute(match):
        target = match.group(1)
        if target.startswith("#"):
            return f"]({REPOSITORY}{target})"
        return f"]({REPOSITORY}/blob/main/{target.removeprefix('./')})"

    return _RELATIVE_LINK.sub(absolute, markdown)


# Reading the long description from README.md
def read_long_description():
    try:
        return _absolute_links((ROOT / "README.md").read_text(encoding="utf-8"))
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
    description=(
        "An open-source coding agent for long-running work: Goals that continue "
        "across turns, a local background service, and TUI, Desktop, Web and "
        "headless clients"
    ),
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
        "server": [],
        "advanced-documents": ["docling>=2.113.0"],
        "test": [
            "pytest>=8,<10",
            "pytest-asyncio>=1,<2",
            # A terminal emulator for tests/e2e: assert on the screen a user
            # sees. LGPLv3, test-only, never shipped.
            "pyte>=0.8.2,<0.9",
        ],
    },
    include_package_data=True,
    package_data={
        "app_server": ["web_assets/*", "web_assets/assets/*"],
        "core.application.goal_prompts": ["*.md"],
        "core.skills": [
            "builtin/*.json",
            "builtin/*/SKILL.md",
            "builtin/*/LICENSE.txt",
            "builtin/*/agents/*.yaml",
            "builtin/*/examples/*",
            "builtin/*/scripts/*",
            "builtin/*/reference/*",
            "builtin/*/references/*",
            "builtin/*/assets/*",
        ],
        "core.agent_presets": ["builtin/*.md"],
        "core.mcp": ["presets.json"],
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
        "Changelog": "https://github.com/HKUDS/DeepCode/blob/main/CHANGELOG.md",
    },
)
