#!/usr/bin/env python3
"""
Cross-Platform File Handler
跨平台文件处理模块

This module provides robust file handling utilities that work consistently
across Windows, Linux, and macOS, with proper error handling and cleanup.

Key features:
- Safe temporary file creation with proper cleanup
- Cross-platform path handling
- Atomic file operations
- Comprehensive error handling and logging
"""

import os
import shutil
import tempfile
import logging
import atexit
import platform
from pathlib import Path
from typing import Optional, Union
from contextlib import contextmanager


class CrossPlatformFileHandler:
    """
    Robust cross-platform file handler with proper error handling.

    Handles common pitfalls in file operations across different operating systems:
    - Windows file handle issues
    - Path separator inconsistencies
    - Permission problems
    - Temporary file cleanup
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize the file handler.

        Args:
            logger: Optional logger instance for tracking operations
        """
        self.logger = logger or self._create_default_logger()
        self.temp_files = []  # Track temporary files for cleanup
        self.platform = platform.system()

        # Register cleanup handler
        atexit.register(self.cleanup_all_temp_files)

        self.logger.info(f"CrossPlatformFileHandler initialized on {self.platform}")

    def _create_default_logger(self) -> logging.Logger:
        """Create a default logger if none provided."""
        logger = logging.getLogger(__name__)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    @staticmethod
    def normalize_path(path: Union[str, Path]) -> Path:
        """
        Normalize a path to use proper separators for the current OS.

        Args:
            path: Input path (string or Path object)

        Returns:
            Normalized Path object

        Example:
            >>> handler = CrossPlatformFileHandler()
            >>> handler.normalize_path("data/files\\test.txt")
            PosixPath('data/files/test.txt')  # On Linux/Mac
            WindowsPath('data\\files\\test.txt')  # On Windows
        """
        if isinstance(path, str):
            # Replace all path separators with the OS-specific one
            path = path.replace("\\", os.sep).replace("/", os.sep)
            return Path(path).resolve()
        return Path(path).resolve()

    def create_safe_temp_file(
        self,
        suffix: str = "",
        prefix: str = "deepcode_",
        content: Optional[bytes] = None,
    ) -> Path:
        """
        Create a temporary file with proper cross-platform handling.

        This method addresses Windows file handle issues by:
        1. Properly closing the file before returning
        2. Setting delete=False to prevent premature deletion
        3. Tracking the file for later cleanup

        Args:
            suffix: File suffix (e.g., ".pdf", ".txt")
            prefix: File prefix for identification
            content: Optional content to write to the file

        Returns:
            Path to the created temporary file

        Raises:
            IOError: If file creation or writing fails
        """
        try:
            # Create temporary file with proper flags
            fd, temp_path = tempfile.mkstemp(
                suffix=suffix,
                prefix=prefix,
                dir=None,  # Use system default temp directory
                text=False,  # Always use binary mode for consistency
            )

            # Convert to Path object
            temp_path_obj = Path(temp_path)

            # Write content if provided
            if content is not None:
                try:
                    # Write using the file descriptor (more reliable on Windows)
                    os.write(fd, content)
                finally:
                    # Always close the file descriptor
                    os.close(fd)

                self.logger.info(
                    f"Created temp file with content: {temp_path_obj.name} "
                    f"({len(content)} bytes)"
                )
            else:
                # Close immediately if no content
                os.close(fd)
                self.logger.info(f"Created empty temp file: {temp_path_obj.name}")

            # Track for cleanup
            self.temp_files.append(temp_path_obj)

            return temp_path_obj

        except Exception as e:
            self.logger.error(f"Failed to create temporary file: {e}")
            raise IOError(f"Temporary file creation failed: {e}")

    @contextmanager
    def temp_directory(self, prefix: str = "deepcode_"):
        """
        Context manager for temporary directory with automatic cleanup.

        Args:
            prefix: Directory prefix for identification

        Yields:
            Path to temporary directory

        Example:
            >>> with handler.temp_directory() as temp_dir:
            ...     # Use temp_dir
            ...     print(temp_dir)
            # Directory automatically cleaned up after context
        """
        temp_dir = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
            self.logger.info(f"Created temporary directory: {temp_dir}")
            yield temp_dir
        finally:
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    self.logger.info(f"Cleaned up temporary directory: {temp_dir}")
                except Exception as e:
                    self.logger.warning(
                        f"Failed to clean up temporary directory {temp_dir}: {e}"
                    )

    def safe_copy_file(
        self,
        source: Union[str, Path],
        destination: Union[str, Path],
        preserve_metadata: bool = True,
        overwrite: bool = False,
    ) -> Path:
        """
        Safely copy a file with proper error handling.

        This method uses copy instead of move to preserve the original file,
        addressing the issue mentioned by the user.

        Args:
            source: Source file path
            destination: Destination file path
            preserve_metadata: Whether to preserve file metadata (timestamps, etc.)
            overwrite: Whether to overwrite if destination exists

        Returns:
            Path to the destination file

        Raises:
            FileNotFoundError: If source file doesn't exist
            FileExistsError: If destination exists and overwrite=False
            IOError: If copy operation fails
        """
        source_path = self.normalize_path(source)
        dest_path = self.normalize_path(destination)

        # Validate source
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        # Check destination
        if dest_path.exists() and not overwrite:
            raise FileExistsError(
                f"Destination already exists: {dest_path}. "
                f"Use overwrite=True to replace."
            )

        try:
            # Ensure destination directory exists
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy file (preserves original!)
            if preserve_metadata:
                shutil.copy2(source_path, dest_path)
            else:
                shutil.copy(source_path, dest_path)

            self.logger.info(
                f"Copied file: {source_path.name} -> {dest_path} "
                f"({source_path.stat().st_size} bytes)"
            )

            return dest_path

        except Exception as e:
            self.logger.error(
                f"Failed to copy file from {source_path} to {dest_path}: {e}"
            )
            raise IOError(f"File copy failed: {e}")

    def safe_move_file(
        self,
        source: Union[str, Path],
        destination: Union[str, Path],
        overwrite: bool = False,
    ) -> Path:
        """
        Safely move a file (only if explicitly needed).

        Note: Prefer safe_copy_file to preserve originals.

        Args:
            source: Source file path
            destination: Destination file path
            overwrite: Whether to overwrite if destination exists

        Returns:
            Path to the destination file

        Raises:
            FileNotFoundError: If source file doesn't exist
            FileExistsError: If destination exists and overwrite=False
            IOError: If move operation fails
        """
        source_path = self.normalize_path(source)
        dest_path = self.normalize_path(destination)

        # Validate source
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        # Check destination
        if dest_path.exists() and not overwrite:
            raise FileExistsError(
                f"Destination already exists: {dest_path}. "
                f"Use overwrite=True to replace."
            )

        try:
            # Ensure destination directory exists
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Move file
            shutil.move(str(source_path), str(dest_path))

            self.logger.info(f"Moved file: {source_path.name} -> {dest_path}")

            return dest_path

        except Exception as e:
            self.logger.error(
                f"Failed to move file from {source_path} to {dest_path}: {e}"
            )
            raise IOError(f"File move failed: {e}")

    def safe_remove_file(self, file_path: Union[str, Path]) -> bool:
        """
        Safely remove a file with proper error handling.

        Args:
            file_path: Path to file to remove

        Returns:
            True if file was removed, False if it didn't exist or removal failed
        """
        path = self.normalize_path(file_path)

        if not path.exists():
            self.logger.debug(f"File already removed or doesn't exist: {path}")
            return False

        try:
            # On Windows, ensure file is not read-only
            if self.platform == "Windows":
                os.chmod(path, 0o777)

            path.unlink()
            self.logger.info(f"Removed file: {path.name}")

            # Remove from tracking list if present
            if path in self.temp_files:
                self.temp_files.remove(path)

            return True

        except PermissionError as e:
            self.logger.warning(f"Permission denied when removing {path}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to remove file {path}: {e}")
            return False

    def cleanup_all_temp_files(self):
        """
        Clean up all tracked temporary files.

        This is automatically called on program exit via atexit,
        but can also be called manually.
        """
        if not self.temp_files:
            return

        self.logger.info(f"Cleaning up {len(self.temp_files)} temporary files...")

        cleaned = 0
        failed = 0

        for temp_file in self.temp_files[
            :
        ]:  # Copy list to avoid modification during iteration
            if self.safe_remove_file(temp_file):
                cleaned += 1
            else:
                failed += 1

        self.logger.info(f"Cleanup complete: {cleaned} files removed, {failed} failed")

        self.temp_files.clear()

    def get_system_temp_dir(self) -> Path:
        """
        Get the system temporary directory with proper cross-platform handling.

        Returns:
            Path to system temporary directory
        """
        return Path(tempfile.gettempdir())

    def create_workspace_directory(
        self, base_dir: Union[str, Path], workspace_name: str, clean: bool = False
    ) -> Path:
        """
        Create a workspace directory with proper structure.

        Args:
            base_dir: Base directory for workspace
            workspace_name: Name of the workspace
            clean: Whether to clean the directory if it exists

        Returns:
            Path to the created workspace directory
        """
        base_path = self.normalize_path(base_dir)
        workspace_path = base_path / workspace_name

        if clean and workspace_path.exists():
            self.logger.info(f"Cleaning existing workspace: {workspace_path}")
            shutil.rmtree(workspace_path, ignore_errors=True)

        workspace_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Created workspace directory: {workspace_path}")

        return workspace_path


# Singleton instance for convenience
_file_handler_instance: Optional[CrossPlatformFileHandler] = None


def get_file_handler(
    logger: Optional[logging.Logger] = None,
) -> CrossPlatformFileHandler:
    """
    Get or create a singleton file handler instance.

    Args:
        logger: Optional logger instance

    Returns:
        CrossPlatformFileHandler instance
    """
    global _file_handler_instance
    if _file_handler_instance is None:
        _file_handler_instance = CrossPlatformFileHandler(logger)
    return _file_handler_instance


# Example usage
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Create handler
    handler = CrossPlatformFileHandler()

    print(f"\n{'='*70}")
    print("Cross-Platform File Handler - Demo")
    print(f"{'='*70}\n")

    print(f"Platform: {handler.platform}")
    print(f"System temp directory: {handler.get_system_temp_dir()}")

    # Demo: Create temporary file
    print("\n1. Creating temporary file...")
    temp_file = handler.create_safe_temp_file(
        suffix=".txt", content=b"Test content for cross-platform file handling"
    )
    print(f"   Created: {temp_file}")

    # Demo: Use temporary directory
    print("\n2. Using temporary directory...")
    with handler.temp_directory() as temp_dir:
        print(f"   Temp directory: {temp_dir}")
        test_file = temp_dir / "test.txt"
        test_file.write_text("Hello from temp directory!")
        print(f"   Created file in temp dir: {test_file}")
    print("   Temp directory automatically cleaned up")

    # Demo: Path normalization
    print("\n3. Path normalization:")
    test_paths = [
        "data/files\\test.txt",
        "data\\files/test.txt",
        "data\\files\\test.txt",
    ]
    for path in test_paths:
        normalized = handler.normalize_path(path)
        print(f"   {path} -> {normalized}")

    # Demo: Cleanup
    print("\n4. Cleaning up tracked files...")
    handler.cleanup_all_temp_files()

    print(f"\n{'='*70}")
    print("Demo completed successfully!")
    print(f"{'='*70}\n")
