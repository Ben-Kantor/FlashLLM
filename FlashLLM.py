#!/usr/bin/env python3
import os, sys, json, asyncio, socket, signal, subprocess, argparse, atexit
from dataclasses import dataclass, field
from typing import Tuple
import urllib.request
import urllib.error

INFO = "FlashLLM by Ben Kantor, Beta 1"
DAEMON_ENV = "LLM_DAEMON"
SOCKET_PREFIX = "\0llm-daemon-"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent"

SYSTEM_PROMPT = """You are a helpful AI assistant.
Be concise and direct in your responses. NEVER use markdown formatting - use plain text only."""


@dataclass
class State:
    pid: int
    context: str = ""


# ------------------- Utility -------------------


def get_socket_path(shell_pid: int) -> str:
    return SOCKET_PREFIX + str(shell_pid)


def run_daemon(shell_pid: int):
    env = dict(os.environ)
    env[DAEMON_ENV] = "1"
    env["LLM_SHELL_PID"] = str(shell_pid)
    subprocess.Popen([sys.executable, __file__], env=env, start_new_session=True)


async def read_stdin():
    if sys.stdin.isatty():
        return None
    return await asyncio.get_event_loop().run_in_executor(None, sys.stdin.read)


async def send_json(writer, obj):
    try:
        if writer.is_closing():
            return False
        writer.write((json.dumps(obj) + "\n").encode())
        await writer.drain()
        return True
    except (ConnectionResetError, BrokenPipeError, OSError):
        return False


async def recv_json(reader):
    try:
        line = await reader.readline()
        if not line:
            return None
        return json.loads(line.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# ------------------- Client -------------------


def parse_args():
    parser = argparse.ArgumentParser(description=INFO, add_help=False)
    parser.add_argument(
        "-s", "--short", action="store_true", help="1-2 sentence response"
    )
    parser.add_argument(
        "-m", "--medium", action="store_true", help="3-5 sentence response"
    )
    parser.add_argument("-l", "--long", action="store_true", help="2-3 paragraphs")
    parser.add_argument(
        "-t", "--thinking", action="store_true", help="Enable extended thinking"
    )
    parser.add_argument(
        "-c", "--clear", action="store_true", help="Clear context and history"
    )
    parser.add_argument(
        "-p",
        "--print",
        action="store_true",
        dest="print_context",
        help="Print context and history",
    )
    parser.add_argument("-h", "--help", action="store_true", help="Show help")
    parser.add_argument("prompt", nargs="*", help="Prompt text")

    return parser.parse_args()


async def run_client():
    args = parse_args()

    if args.help:
        print(f"{INFO}")
        print("\nUsage: llm [OPTIONS] [PROMPT]")
        print("\nOptions:")
        print("  -s, --short      1-2 sentence response")
        print("  -m, --medium     3-5 sentence response")
        print("  -l, --long       2-3 paragraphs")
        print("  -t, --thinking   Enable extended thinking (4096 tokens)")
        print("  -c, --clear      Clear context and history")
        print("  -p, --print      Print current context and history")
        print("  -h, --help       Show this help message")
        print("\nContext information can be inputted via the standard input.")
        return

    shell_pid = os.getppid()
    socket_path = get_socket_path(shell_pid)

    stdin_data = await read_stdin()
    user_prompt = " ".join(args.prompt).strip()

    # No-args behavior
    if not stdin_data and not user_prompt and not args.print_context and not args.clear:
        print(f"[Info] {INFO}")
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path)
            writer.close()
            await writer.wait_closed()
        except Exception:
            print(f"[Info] Daemon not running")
        return

    # Connect to daemon
    reader, writer = None, None
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
    except Exception:
        run_daemon(shell_pid)
        # Wait for daemon to start
        for _ in range(20):
            try:
                await asyncio.sleep(0.1)
                reader, writer = await asyncio.open_unix_connection(socket_path)
                break
            except Exception:
                continue
        else:
            print("[Error] Daemon failed to start.", file=sys.stderr)
            return

    # Determine length constraint
    length = None
    if args.short:
        length = "short"
    elif args.medium:
        length = "medium"
    elif args.long:
        length = "long"

    payload = {
        "stdin": stdin_data,
        "prompt": user_prompt,
        "length": length,
        "thinking": args.thinking,
        "clear": args.clear,
        "print_context": args.print_context,
    }
    
    if not await send_json(writer, payload):
        print("[Error] Failed to send request to daemon.", file=sys.stderr)
        return

    try:
        while True:
            msg = await recv_json(reader)
            if msg is None:
                break
            t = msg.get("type")
            if t == "chunk":
                print(msg["data"], end="", flush=True)
            elif t == "error":
                print(f"[Error] {msg['data']}", file=sys.stderr)
            elif t == "context_info":
                print(msg["data"])
            elif t == "done":
                break
            elif t == "interrupted":
                print(f"\n[Interrupted] {msg['data']}", file=sys.stderr)
                break
    except Exception as e:
        print(f"[Error] Communication error: {e}", file=sys.stderr)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ------------------- Daemon -------------------


def parse_sse_stream(response):
    """Parse Server-Sent Events stream from the response."""
    buffer = b""
    for chunk in response:
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if line.startswith(b"data: "):
                data = line[6:].decode("utf-8")
                if data.strip():
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue
    
    # Process any remaining data in buffer
    if buffer.strip().startswith(b"data: "):
        data = buffer[6:].decode("utf-8")
        if data.strip():
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                pass


async def stream_gemini_api(full_prompt, api_key, thinking_budget, stop_event):
    # Build request payload
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"thinkingConfig": {"thinkingBudget": thinking_budget}},
    }

    url = f"{GEMINI_API_URL}?alt=sse&key={api_key}"

    # Prepare request
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    loop = asyncio.get_event_loop()

    def blocking_request():
        chunks = []
        response = None
        try:
            response = urllib.request.urlopen(req, timeout=60)
            for event in parse_sse_stream(response):
                if stop_event.is_set():
                    return chunks, True  # Return interrupted flag

                # Extract text from the response
                candidates = event.get("candidates", [])
                if candidates:
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    for part in parts:
                        if "text" in part:
                            chunks.append(part["text"])
        except urllib.error.HTTPError as e:
            try:
                error_body = e.read().decode("utf-8")
                raise Exception(f"HTTP {e.code}: {error_body}")
            except:
                raise Exception(f"HTTP {e.code}: Unable to read error response")
        except urllib.error.URLError as e:
            raise Exception(f"Network error: {str(e)}")
        except Exception as e:
            raise Exception(f"API request failed: {str(e)}")
        finally:
            if response:
                try:
                    response.close()
                except:
                    pass
        
        return chunks, False  # Not interrupted

    # Run the blocking request in executor
    try:
        result = await loop.run_in_executor(None, blocking_request)
        text_chunks, interrupted = result
        
        for chunk in text_chunks:
            if stop_event.is_set():
                break
            yield chunk, interrupted
            
    except Exception as e:
        raise e


async def daemon_handle(reader, writer, state: State):
    response_started = False
    response_text = ""
    user_input = ""
    
    try:
        data = await recv_json(reader)
        if not data:
            writer.close()
            return

        # Handle flags
        clear_context = data.get("clear", False)
        print_context = data.get("print_context", False)
        length = data.get("length")
        thinking = data.get("thinking", False)

        # Clear if requested
        if clear_context:
            state.context = ""

        # Print context if requested
        if print_context:
            output = state.context if state.context.strip() else "(empty)"
            await send_json(writer, {"type": "context_info", "data": output})
            await send_json(writer, {"type": "done"})
            if not writer.is_closing():
                writer.close()
            return

        user_input = data.get("prompt") or ""
        stdin_data = data.get("stdin")

        # Add stdin as context
        if stdin_data:
            if state.context:
                state.context += "\n\n"
            state.context += f"[Context]\n{stdin_data.strip()}"

        # If no prompt provided, just acknowledge context addition
        if not user_input:
            if stdin_data:
                await send_json(writer, {"type": "chunk", "data": "[Context Added]"})
            await send_json(writer, {"type": "done"})
            if not writer.is_closing():
                writer.close()
            return

        # Get API key
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            await send_json(
                writer,
                {"type": "error", "data": "GEMINI_API_KEY environment variable not set"},
            )
            writer.close()
            return

        # Ctrl+C handling
        stop_event = asyncio.Event()

        def stop_gen(sig, frame):
            asyncio.get_event_loop().call_soon_threadsafe(stop_event.set)

        old_handler = signal.signal(signal.SIGINT, stop_gen)

        try:
            # Build full prompt with memory
            prompt_parts = []

            # Add system prompt with length constraint
            system_with_length = SYSTEM_PROMPT
            if length == "short":
                system_with_length += "\nProvide a response in 1-2 sentences only."
            elif length == "medium":
                system_with_length += "\nProvide a response in 3-5 sentences."
            elif length == "long":
                system_with_length += "\nProvide a response in 2-3 paragraphs."
            else:
                system_with_length += (
                    "\nProvide a response in 1-2 sentences or longer if needed."
                )

            prompt_parts.append(system_with_length)

            # Add existing context if available
            if state.context:
                prompt_parts.append("\n=== Conversation ===")
                prompt_parts.append(state.context)

            # Add current user message
            prompt_parts.append(f"\n[Prompt]\n{user_input.strip()}")
            prompt_parts.append("\n[Response]")

            full_prompt = "\n".join(prompt_parts)

            thinking_budget = 4096 if thinking else 0

            response_started = True
            interrupted = False
            
            async for chunk, was_interrupted in stream_gemini_api(
                full_prompt, api_key, thinking_budget, stop_event
            ):
                if stop_event.is_set() or was_interrupted:
                    interrupted = True
                    break

                response_text += chunk
                if not await send_json(writer, {"type": "chunk", "data": chunk}):
                    # Client disconnected
                    interrupted = True
                    break

            if interrupted:
                await send_json(
                    writer,
                    {"type": "interrupted", "data": "Generation stopped"},
                )
            else:
                # Only update context if generation completed successfully
                if state.context:
                    state.context += "\n\n"
                state.context += f"[Prompt]\n{user_input.strip()}\n\n[Response]\n{response_text.strip()}"
                await send_json(writer, {"type": "done"})

        finally:
            signal.signal(signal.SIGINT, old_handler)

    except Exception as e:
        error_msg = str(e)
        await send_json(writer, {"type": "error", "data": error_msg})
        # Don't add to context if there was an error
    finally:
        try:
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()
        except Exception:
            pass


async def check_shell_alive(shell_pid: int):
    while True:
        await asyncio.sleep(1)
        try:
            os.kill(shell_pid, 0)  # check shell is alive
        except ProcessLookupError:
            os._exit(0)


async def run_daemon_loop(shell_pid: int):
    state = State(pid=shell_pid)
    socket_path = get_socket_path(shell_pid)
    
    # Cleanup function for socket
    def cleanup_socket():
        try:
            # For abstract sockets (Linux), this isn't necessary
            # but for compatibility we keep it
            if not socket_path.startswith("\0"):
                if os.path.exists(socket_path):
                    os.unlink(socket_path)
        except Exception:
            pass
    
    # Register cleanup
    atexit.register(cleanup_socket)
    
    # Also handle signals
    def signal_handler(sig, frame):
        cleanup_socket()
        os._exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    
    server = await asyncio.start_unix_server(
        lambda r, w: daemon_handle(r, w, state), path=socket_path
    )
    asyncio.create_task(check_shell_alive(shell_pid))
    
    try:
        async with server:
            await server.serve_forever()
    finally:
        cleanup_socket()


# ------------------- Entrypoint -------------------


def main():
    if os.getenv(DAEMON_ENV) == "1":
        shell_pid = int(os.getenv("LLM_SHELL_PID", "0"))
        try:
            asyncio.run(run_daemon_loop(shell_pid))
        except KeyboardInterrupt:
            pass
    else:
        try:
            asyncio.run(run_client())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
