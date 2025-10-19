# FlashLLM
A lightweight python script to run llm prompts quickly from a unix shell.

## Installation
Download the file called 'FlashLLM.py', move it to a directory in path such as /bin/ and rename it to just 'llm' or a similar convinient name.

Export your API key as an environment variable called GEMINI_API_KEY, using .bashrc or the startup file of your shell of choice.

## Usage

Simply run `llm` followed by your prompt.

### Basic Prompt

llm What is the capital of France?

### Adjusting Response Length

*   **Short (1-2 sentences)**:
    llm -s Explain quantum entanglement simply.

*   **Medium (3-5 sentences)**:
    llm -m Describe the benefits of a healthy diet.

*   **Long (2-3 paragraphs)**:
    llm -l Discuss the impact of climate change on biodiversity.

### Clearing Context

To clear the conversation history for the current shell session:
llm -c

### Printing Current Context

To view the current conversation context:
llm -p

### Using Standard Input for Context

You can pipe content from stdin to provide additional context for your prompt:
cat my_document.txt | llm Summarize this document.

### Extended Thinking

For more complex prompts that might benefit from additional processing time:
llm -t What are the philosophical implications of AI sentience?

### Getting Help

llm -h

## How it Works

Shellm operates with a client-daemon architecture. When you run `llm` for the first time in a shell session, it starts a background daemon process unique to that shell's PID. Subsequent `llm` commands connect to this daemon, which manages the conversation context and interacts with the Gemini API. The daemon automatically shuts down when your shell session ends.
