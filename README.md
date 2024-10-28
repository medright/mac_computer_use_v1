# Anthropic Computer Use (for Mac)

[Anthropic Computer Use](https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/README.md) is a beta Anthropic feature which runs a Docker image with Ubuntu and controls it. This fork allows you to run it natively on macOS, providing direct system control through native macOS commands and utilities.

> [!WARNING]
> **Security Considerations:**
> - This tool requires extensive system permissions and can control your entire Mac
> - Never share or commit your API keys
> - Run this tool only in controlled environments
> - Monitor system activity during use
> - Review the [SECURITY.md](SECURITY.md) file for complete security guidelines

## Features

- Native macOS GUI interaction (no Docker required)
- Screen capture using native macOS commands
- Keyboard and mouse control through cliclick
- Multiple LLM provider support (Anthropic, Bedrock, Vertex)
- Streamlit-based interface
- Automatic screen resolution scaling
- File system interaction and editing capabilities

## System Requirements

- macOS Sonoma 15.7 or later
- Python 3.12+
- Homebrew (for installing additional dependencies)
- cliclick (`brew install cliclick`) - Required for mouse and keyboard control

### Required Permissions
- Screen Recording
- Accessibility
- Input Monitoring
- Full Disk Access (for certain operations)

To grant these permissions:
1. Go to System Preferences > Security & Privacy > Privacy
2. Enable permissions for your terminal application or IDE

## Rate Limits

- Anthropic API: Default 50 requests per minute
- Screen capture: Maximum 1 capture per second
- System commands: Throttled to prevent system overload
- See [rate_limiter.py](tools/rate_limiter.py) for detailed limits

## Setup Instructions

1. Clone the repository and navigate to it:

```bash
git clone https://github.com/deedy/mac_computer_use.git
cd mac_computer_use
```

2. Run the setup script:

```bash
chmod +x setup.sh
./setup.sh
```

This will:
- Install system dependencies if needed (Homebrew, Python 3.12, cliclick)
- Create and activate a Python virtual environment
- Install all required Python packages
- Create an activation script

## Running the Demo

### Set up your environment and Anthropic API key

1. Copy the sample environment file and configure your settings:

```bash
cp .sample.env .env
```

2. Edit the `.env` file with your settings. At minimum, you'll need:
- Your Anthropic API key from [Anthropic Console](https://console.anthropic.com/settings/keys)
- Desired screen dimensions (recommended: stay within XGA/WXGA resolution)

Example minimal configuration:
```
API_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_key_here
WIDTH=1280
HEIGHT=800
DISPLAY_NUM=1
```

For other API providers (Bedrock, Vertex), refer to the additional settings in `.sample.env`.

3. Activate the environment:

```bash
source activate.sh
```

4. Start the Streamlit app:

```bash
streamlit run streamlit.py
```

The interface will be available at http://localhost:8501

## Screen Size Considerations

We recommend using one of these resolutions for optimal performance:

-   XGA: 1024x768 (4:3)
-   WXGA: 1280x800 (16:10)
-   FWXGA: 1366x768 (~16:9)

Higher resolutions will be automatically scaled down to these targets to optimize model performance. You can set the resolution using environment variables:

```bash
export WIDTH=1024
export HEIGHT=768
streamlit run streamlit.py
```

> [!IMPORTANT]
> The Beta API used in this reference implementation is subject to change. Please refer to the [API release notes](https://docs.anthropic.com/en/release-notes/api) for the most up-to-date information.

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

## Security

For security-related matters, please review our [Security Policy](SECURITY.md).
