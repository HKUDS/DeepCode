# DeepCode CLI Commands

## Overview

The DeepCode CLI has been restructured using Click to support multiple subcommands. This allows for better organization and extensibility.

## Available Commands

### Main Command Group

```bash
python cli/cli_app.py --help
```

Shows all available commands and options.

### Run Interactive Session (Default)

```bash
# All of these are equivalent:
python cli/cli_app.py
python cli/cli_app.py run
```

Launches the interactive DeepCode CLI session where you can:
- Process research papers from URLs
- Upload and process local files
- Chat with the AI to generate code
- View processing history
- Configure settings

### Configuration Management

```bash
python cli/cli_app.py config
```

Shows configuration options (placeholder for future implementation).

**Planned features:**
- View current configuration
- Set default processing mode (comprehensive/optimized)
- Configure API keys and endpoints
- Manage workspace settings

### Cleanup Utility

```bash
# Clean Python cache files
python cli/cli_app.py clean --cache

# Clean log files
python cli/cli_app.py clean --logs

# Clean everything
python cli/cli_app.py clean --all
```

Removes temporary files and caches to free up disk space.

## Adding New Subcommands

To add a new subcommand, simply add a new function decorated with `@cli.command()` in `cli/cli_app.py`:

```python
@cli.command()
@click.option('--option-name', help='Description')
def my_command(option_name):
    """Description of what this command does"""
    # Your implementation here
    click.echo("Command executed!")
```

## Examples

### Basic Usage

```bash
# Start interactive session
python cli/cli_app.py

# Or explicitly use the run command
python cli/cli_app.py run
```

### Cleanup Examples

```bash
# Clean only cache files
python cli/cli_app.py clean --cache

# Clean only logs
python cli/cli_app.py clean --logs

# Clean everything
python cli/cli_app.py clean --all
```

### Getting Help

```bash
# General help
python cli/cli_app.py --help

# Help for specific command
python cli/cli_app.py clean --help
python cli/cli_app.py config --help
```

## Version Information

```bash
python cli/cli_app.py --version
```

## Migration Notes

### For Developers

The previous `main()` coroutine has been renamed to `run_interactive_cli()` and is now wrapped by Click commands:

- **Old:** `asyncio.run(main())`
- **New:** `cli()` (Click group) → `run()` command → `run_interactive_cli()`

### Backward Compatibility

The CLI launcher (`cli_launcher.py`) has been updated to use the new Click-based structure, maintaining backward compatibility with existing workflows.

## Future Enhancements

Potential subcommands to add:
- `deepcode process --file <path>` - Direct file processing
- `deepcode process --url <url>` - Direct URL processing
- `deepcode history` - View processing history
- `deepcode export` - Export results
- `deepcode doctor` - Check system dependencies and configuration

