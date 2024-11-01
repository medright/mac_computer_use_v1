"""
Agentic sampling loop that calls the Anthropic API and local implementation of anthropic-defined computer use tools.
"""

import platform
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from anthropic import Anthropic, AnthropicBedrock, AnthropicVertex, APIResponse
from anthropic.types import (
    ToolResultBlockParam,
)
from anthropic.types.beta import (
    BetaContentBlock,
    BetaContentBlockParam,
    BetaImageBlockParam,
    BetaMessage,
    BetaMessageParam,
    BetaTextBlockParam,
    BetaToolResultBlockParam,
)

from tools import BashTool, ComputerTool, EditTool, ToolCollection, ToolResult
from tools.rate_limiter import RateLimiter
import tiktoken
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx
import asyncio
import json
from pathlib import Path

BETA_FLAG = "computer-use-2024-10-22"


class APIProvider(StrEnum):
    ANTHROPIC = "anthropic"
    BEDROCK = "bedrock"
    VERTEX = "vertex"


PROVIDER_TO_DEFAULT_MODEL_NAME: dict[APIProvider, str] = {
    APIProvider.ANTHROPIC: "claude-3-5-sonnet-20241022",
    APIProvider.BEDROCK: "anthropic.claude-3-5-sonnet-20241022-v2:0",
    APIProvider.VERTEX: "claude-3-5-sonnet-v2@20241022",
}


# This system prompt is optimized for the Docker environment in this repository and
# specific tool combinations enabled.
# We encourage modifying this system prompt to ensure the model has context for the
# environment it is running in, and to provide any additional information that may be
# helpful for the task at hand.
SYSTEM_PROMPT = f"""<SYSTEM_CAPABILITY>
* You are utilizing a macOS Sonoma 15.7 environment using {platform.machine()} architecture with internet access.
* You can install applications using homebrew with your bash tool. Use curl instead of wget.
* To open Safari, please just click on the Safari icon in the Dock or use Spotlight. You can also use `open -a Safari`.
* Using bash tool you can start GUI applications. GUI apps can be launched directly or with `open -a "Application Name"`. GUI apps will appear natively within macOS, but they may take some time to appear. Take a screenshot to confirm it did.
* When using your bash tool with commands that are expected to output very large quantities of text, redirect into a tmp file and use str_replace_editor or `grep -n -B <lines before> -A <lines after> <query> <filename>` to confirm output.
* When viewing a page in Safari, it can be helpful to zoom out so that you can see everything on the page. In Safari, use Command + "-" to zoom out or Command + "+" to zoom in.
* When using your computer function calls, they take a while to run and send back to you. Where possible/feasible, try to chain multiple of these calls all into one function calls request.
* The current date is {datetime.today().strftime('%A, %B %-d, %Y')}.
</SYSTEM_CAPABILITY>
<IMPORTANT>
* When using Safari, if any first-time setup dialogs appear, IGNORE THEM. Instead, click directly in the address bar and enter the appropriate search term or URL there.
* If the item you are looking at is a pdf, if after taking a single screenshot of the pdf it seems that you want to read the entire document instead of trying to continue to read the pdf from your screenshots + navigation, determine the URL, use curl to download the pdf, install and use pdftotext (available via homebrew) to convert it to a text file, and then read that text file directly with your StrReplaceEditTool.
</IMPORTANT>"""
# SYSTEM_PROMPT = f"""<SYSTEM_CAPABILITY>
# * You are utilizing a macOS Sonoma 15.7 environment using {platform.machine()} architecture with command line internet access.
# * Package management:
#   - Use homebrew for package installation
#   - Use curl for HTTP requests
#   - Use npm/yarn for Node.js packages
#   - Use pip for Python packages

# * Browser automation available via Playwright:
#   - Supports Chrome, Firefox, and WebKit
#   - Can handle JavaScript-heavy applications
#   - Capable of screenshots, navigation, and interaction
#   - Handles dynamic content loading

# * System automation:
#   - cliclick for simulating mouse/keyboard input
#   - osascript for AppleScript commands
#   - launchctl for managing services
#   - defaults for reading/writing system preferences

# * Development tools:
#   - Standard Unix/Linux command line utilities
#   - Git for version control
#   - Docker for containerization
#   - Common build tools (make, cmake, etc.)

# * Output handling:
#   - For large output, redirect to tmp files: command > /tmp/output.txt
#   - Use grep with context: grep -n -B <before> -A <after> <query> <filename>
#   - Stream processing with awk, sed, and other text utilities

# * Note: Command line function calls may have latency. Chain multiple operations into single requests where feasible.

# * The current date is {datetime.today().strftime('%A, %B %-d, %Y')}.
# </SYSTEM_CAPABILITY>"""

# Add this function to estimate tokens
def estimate_tokens(messages: list[BetaMessageParam], system: str) -> int:
    """Estimate the number of tokens in the request"""
    # Use cl100k_base encoding which Claude uses
    enc = tiktoken.get_encoding("cl100k_base")
    
    total = len(enc.encode(system))
    
    for msg in messages:
        if isinstance(msg["content"], str):
            total += len(enc.encode(msg["content"]))
        elif isinstance(msg["content"], list):
            for block in msg["content"]:
                if isinstance(block, dict):
                    if block["type"] == "text":
                        total += len(enc.encode(block["text"]))
                    elif block["type"] == "tool_result":
                        if isinstance(block["content"], str):
                            total += len(enc.encode(block["content"]))
                        elif isinstance(block["content"], list):
                            for content in block["content"]:
                                if content["type"] == "text":
                                    total += len(enc.encode(content["text"]))
    
    return total

# Add this function near the top with other helper functions
def serialize_message_content(content):
    """Convert message content to JSON-serializable format"""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        return [
            {
                "type": block["type"],
                "text": block["text"] if block["type"] == "text" else None,
                "content": block["content"] if block["type"] == "tool_result" else None,
                "tool_use_id": block.get("tool_use_id"),
                "is_error": block.get("is_error", False)
            }
            for block in content if isinstance(block, dict)
        ]
    return str(content)  # Fallback for unknown types

# Update the sampling_loop function
async def sampling_loop(
    *,
    model: str,
    provider: APIProvider,
    system_prompt_suffix: str,
    messages: list[BetaMessageParam],
    output_callback: Callable[[BetaContentBlock], None],
    tool_output_callback: Callable[[ToolResult, str], None],
    api_response_callback: Callable[[APIResponse[BetaMessage]], None],
    api_key: str,
    only_n_most_recent_images: int | None = None,
    max_tokens: int = 4096,
):
    # Add Streamlit context to this async function
    add_script_run_ctx()
    
    """
    Agentic sampling loop for the assistant/tool interaction of computer use.
    """
    # Use the existing rate limiter from session state
    rate_limiter = st.session_state.rate_limiter
    
    tool_collection = ToolCollection(
        ComputerTool(),
        BashTool(),
        EditTool(),
    )
    system = (
        f"{SYSTEM_PROMPT}{' ' + system_prompt_suffix if system_prompt_suffix else ''}"
    )

    while True:
        try:
            if only_n_most_recent_images:
                _maybe_filter_to_n_most_recent_images(messages, only_n_most_recent_images)

            # Estimate input tokens (system prompt + messages)
            input_tokens = estimate_tokens(messages, system)
            
            # Wait for rate limits if needed
            await rate_limiter.wait_if_needed(model, input_tokens)

            if provider == APIProvider.ANTHROPIC:
                client = Anthropic(
                    api_key=api_key,
                    base_url="https://api.anthropic.com",
                    timeout=60.0,
                )
            elif provider == APIProvider.VERTEX:
                client = AnthropicVertex()
            elif provider == APIProvider.BEDROCK:
                client = AnthropicBedrock()

            # Call the API
            raw_response = client.beta.messages.with_raw_response.create(
                max_tokens=max_tokens,
                messages=messages,
                model=model,
                system=system,
                tools=tool_collection.to_params(),
                betas=[BETA_FLAG],
            )

            response = raw_response.parse()
            
            # Get actual output tokens from response
            output_tokens = response.usage.output_tokens if hasattr(response, 'usage') else max_tokens
            
            # Record the usage with actual token counts
            rate_limiter.record_usage(
                model, 
                input_tokens=input_tokens,
                output_tokens=output_tokens
            )

            api_response_callback(cast(APIResponse[BetaMessage], raw_response))

            messages.append(
                {
                    "role": "assistant",
                    "content": cast(list[BetaContentBlockParam], response.content),
                }
            )

            # Save conversation history with serialized content
            conversation_history = {
                "timestamp": datetime.now().isoformat(),
                "messages": [
                    {
                        "role": msg["role"],
                        "content": serialize_message_content(msg["content"])
                    }
                    for msg in messages[:-1]  # Exclude the last message if it's from assistant
                ]
            }
            
            with open('conversation_history.json', 'w') as f:
                json.dump([conversation_history], f, indent=2)

            tool_result_content: list[BetaToolResultBlockParam] = []
            for content_block in cast(list[BetaContentBlock], response.content):
                print("CONTENT", content_block)
                output_callback(content_block)
                if content_block.type == "tool_use":
                    result = await tool_collection.run(
                        name=content_block.name,
                        tool_input=cast(dict[str, Any], content_block.input),
                    )
                    tool_result_content.append(
                        _make_api_tool_result(result, content_block.id)
                    )
                    tool_output_callback(result, content_block.id)

            if not tool_result_content:
                # Save final conversation state before returning
                conversation_history = {
                    "timestamp": datetime.now().isoformat(),
                    "messages": [
                        {
                            "role": msg["role"],
                            "content": serialize_message_content(msg["content"])
                        }
                        for msg in messages
                    ]
                }
                
                with open('conversation_history.json', 'w') as f:
                    json.dump([conversation_history], f, indent=2)
                return messages

            messages.append({"content": tool_result_content, "role": "user"})
            
            # Save conversation history after tool results
            conversation_history = {
                "timestamp": datetime.now().isoformat(),
                "messages": [
                    {
                        "role": msg["role"],
                        "content": serialize_message_content(msg["content"])
                    }
                    for msg in messages[:-1]  # Exclude the last message if it's from assistant
                ]
            }
            
            with open('conversation_history.json', 'w') as f:
                json.dump([conversation_history], f, indent=2)
            
        except Exception as e:
            error_message = f"API Error: {str(e)}\n\nRetrying in 5 seconds..."
            st.error(error_message)
            await asyncio.sleep(5)
            continue


def _maybe_filter_to_n_most_recent_images(
    messages: list[BetaMessageParam],
    images_to_keep: int,
    min_removal_threshold: int = 10,
):
    """
    With the assumption that images are screenshots that are of diminishing value as
    the conversation progresses, remove all but the final `images_to_keep` tool_result
    images in place, with a chunk of min_removal_threshold to reduce the amount we
    break the implicit prompt cache.
    """
    if images_to_keep is None:
        return messages

    tool_result_blocks = cast(
        list[ToolResultBlockParam],
        [
            item
            for message in messages
            for item in (
                message["content"] if isinstance(message["content"], list) else []
            )
            if isinstance(item, dict) and item.get("type") == "tool_result"
        ],
    )

    total_images = sum(
        1
        for tool_result in tool_result_blocks
        for content in tool_result.get("content", [])
        if isinstance(content, dict) and content.get("type") == "image"
    )

    images_to_remove = total_images - images_to_keep
    # for better cache behavior, we want to remove in chunks
    images_to_remove -= images_to_remove % min_removal_threshold

    for tool_result in tool_result_blocks:
        if isinstance(tool_result.get("content"), list):
            new_content = []
            for content in tool_result.get("content", []):
                if isinstance(content, dict) and content.get("type") == "image":
                    if images_to_remove > 0:
                        images_to_remove -= 1
                        continue
                new_content.append(content)
            tool_result["content"] = new_content


def _make_api_tool_result(
    result: ToolResult, tool_use_id: str
) -> BetaToolResultBlockParam:
    """Convert an agent ToolResult to an API ToolResultBlockParam."""
    tool_result_content: list[BetaTextBlockParam | BetaImageBlockParam] | str = []
    is_error = False
    if result.error:
        is_error = True
        tool_result_content = _maybe_prepend_system_tool_result(result, result.error)
    else:
        if result.output:
            tool_result_content.append(
                {
                    "type": "text",
                    "text": _maybe_prepend_system_tool_result(result, result.output),
                }
            )
        if result.base64_image:
            tool_result_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": result.base64_image,
                    },
                }
            )
    return {
        "type": "tool_result",
        "content": tool_result_content,
        "tool_use_id": tool_use_id,
        "is_error": is_error,
    }


def _maybe_prepend_system_tool_result(result: ToolResult, result_text: str):
    if result.system:
        result_text = f"<system>{result.system}</system>\n{result_text}"
    return result_text


# Add this near the top with other imports
from streamlit.runtime.scriptrunner import add_script_run_ctx

# Modify the wait_if_needed method to show messages
async def wait_if_needed(self, model: str, token_count: int):
    """Wait until rate limits allow the request and show status in Streamlit UI"""
    while True:
        error = self.check_limits(model, token_count)
        if not error:
            break
            
        # Show warning message in Streamlit
        st.warning(f"Rate limit reached: {error}. Waiting...", icon="⏳")
        await asyncio.sleep(1)

