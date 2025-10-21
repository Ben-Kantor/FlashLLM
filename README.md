# PipeLLM
A lightweight python script to allow the usage of LLMs from a unix shell, with compatiblity for data piping, sessions that last as long as the shell they are in, and exportable, customizable context windows. Current only compatible with Google Gemini. 

## Installation
Download the script, move it to a directory in your `PATH` (such as `~/.local/bin/` or `/usr/local/bin/`), and rename it to `llm` for convenience. Make sure the script is executable (`chmod +x llm`).

Export your API key as an environment variable called `GEMINI_API_KEY` in your `.bashrc`, `.zshrc`, or the startup file for your shell of choice.
`export GEMINI_API_KEY="YOUR_API_KEY_HERE"`

## Usage

`llm [PROMPT]` - Send a basic prompt.

`llm -i [PROMPT]` - Run in isolated mode. Ignores and does not save chat history.

`llm -c` - Clear the current context and chat history.

`llm -p` - Print the current context and chat history.

`llm -s [PROMPT]` - Get a short, 1-2 sentence response.

`llm -m [PROMPT]` - Get a medium, 3-5 sentence response.

`llm -l [PROMPT]` - Get a long, 2-3 paragraph response.

`llm -t [PROMPT]` - Enable extended model thinking.

`echo "CONTEXT" | llm [PROMPT]` - Pipe a string as context for the prompt.

## How it Works

FlashLLM operates with a client-daemon architecture. When you run `llm` for the first time in a shell session, it starts a lightweight background daemon process unique to that shell's PID.

For standard prompts, the `llm` client communicates with the daemon to retrieve the current conversation history. The client then calls the Gemini API, displays the response, and sends the updated conversation back to the daemon for storage.

When using the isolated (`-i`) flag, the client bypasses the daemon entirely, sending a one-off request to the API without affecting the stored conversation history.

The daemon's sole responsibility is to store context, and it automatically shuts down when your shell session ends.
