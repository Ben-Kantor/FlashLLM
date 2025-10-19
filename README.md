# FlashLLM
A lightweight python script to run llm prompts quickly from a unix shell.

## Installation
Download the file called 'FlashLLM.py', move it to a directory in path such as /bin/ and rename it to just 'llm' or a similar convinient name.

Export your API key as an environment variable called GEMINI_API_KEY, using .bashrc or the startup file of your shell of choice.

## Usage

llm \[PROMPT\] - Send a basic prompt.
llm -c \[PROMPT\] - Clear context, then send a prompt.
llm -p - Print the current context and chat history.
llm -s \[PROMPT\] - Get a short, 1-2 sentence response.
llm -m \[PROMPT\] - Get a medium, 3-5 sentence response.
llm -l \[PROMPT\] - Get a long, 2-3 paragraph response.
llm -t \[PROMPT\] - Enable model thinking.
echo "CONTEXT" | llm \[PROMPT\] - Pipe context to the LLM.

## How it Works

Shellm operates with a client-daemon architecture. When you run `llm` for the first time in a shell session, it starts a background daemon process unique to that shell's PID. Subsequent `llm` commands connect to this daemon, which manages the conversation context and interacts with the Gemini API. The daemon automatically shuts down when your shell session ends.
