#!/usr/bin/env python3
import os, sys, json, asyncio, socket, signal, subprocess, argparse, atexit
from dataclasses import dataclass
import urllib.request, urllib.error

INFO = "PipeLLM by Ben Kantor, Beta 2"
DAEMON_ENV = "LLM_DAEMON"
SOCKET_PREFIX = "\0llm-daemon-"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

SYSTEM_PROMPT = """You are a helpful AI assistant.
Be concise and direct in your responses. NEVER use markdown formatting - use plain text only."""


@dataclass
class State:
    pid: int
    context: str = ""


# ------------------- Utilities -------------------


def get_socket_path(shell_pid: int) -> str:
    return SOCKET_PREFIX + str(shell_pid)


def run_daemon(shell_pid: int):
    env = dict(os.environ)
    env[DAEMON_ENV] = "1"
    env["LLM_SHELL_PID"] = str(shell_pid)
    # Redirect daemon's stdout and stderr to null to keep the client's terminal clean
    subprocess.Popen(
        [sys.executable, __file__],
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def read_stdin():
    if sys.stdin.isatty():
        return None
    return await asyncio.get_event_loop().run_in_executor(None, sys.stdin.read)


async def send_json(writer, obj):
    try:
        writer.write((json.dumps(obj) + "\n").encode())
        await writer.drain()
        return True
    except (BrokenPipeError, ConnectionResetError):
        return False


async def recv_json(reader):
    try:
        line = await reader.readline()
        if not line:
            return None
        return json.loads(line.decode())
    except (BrokenPipeError, ConnectionResetError, asyncio.IncompleteReadError):
        return None


# ------------------- API Call Function (used by Client) -------------------


def call_gemini(full_prompt, api_key, thinking_budget):
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"thinkingConfig": {"thinkingBudget": thinking_budget}},
    }
    req = urllib.request.Request(
        GEMINI_API_URL + f"?key={api_key}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            cands = data.get("candidates", [])
            if cands:
                content = cands[0].get("content", {})
                if content:
                    parts = content.get("parts", [])
                    return "".join(p.get("text", "") for p in parts)
            # Handle cases where the response is blocked or has no candidates
            return f"[No output or response blocked]\n{json.dumps(data)}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode(errors='ignore')}"
    except urllib.error.URLError as e:
        return f"Network error: {e.reason}"
    except Exception as e:
        return f"Error: {e}"


# ------------------- Client -------------------


def parse_args():
    p = argparse.ArgumentParser(description=INFO, add_help=False)
    p.add_argument("-s", "--short", action="store_true")
    p.add_argument("-m", "--medium", action="store_true")
    p.add_argument("-l", "--long", action="store_true")
    p.add_argument("-t", "--thinking", action="store_true")
    p.add_argument("-c", "--clear", action="store_true")
    p.add_argument("-p", "--print", dest="print_context", action="store_true")
    p.add_argument(
        "-i",
        "--isolated",
        action="store_true",
        help="Isolated mode: ignore and don't save context",
    )
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("prompt", nargs="*")
    return p.parse_args()


async def run_client():
    args = parse_args()
    if args.help:
        print(f"{INFO}\nUsage: llm [OPTIONS] [PROMPT]\n")
        print("  -s, --short      1-2 sentence response")
        print("  -m, --medium     3-5 sentence response")
        print("  -l, --long       2-3 paragraphs")
        print("  -t, --thinking   Enable extended thinking (4096 tokens)")
        print("  -i, --isolated   Isolated mode: ignore and don't save context")
        print("  -c, --clear      Clear context and history")
        print("  -p, --print      Print context and history")
        return

    stdin_data = await read_stdin()
    user_prompt = " ".join(args.prompt).strip()

    # --- ISOLATED MODE ---
    if args.isolated:
        if not stdin_data and not user_prompt:
            print("[Error] Isolated mode requires a prompt or stdin.", file=sys.stderr)
            return

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("[Error] Missing GEMINI_API_KEY", file=sys.stderr)
            return

        system = SYSTEM_PROMPT
        l = (
            "short"
            if args.short
            else "medium"
            if args.medium
            else "long"
            if args.long
            else None
        )
        if l == "short":
            system += "\nRespond in 1-2 sentences."
        elif l == "medium":
            system += "\nRespond in 3-5 sentences."
        elif l == "long":
            system += "\nRespond in 2-3 paragraphs."

        context = f"[Context]\n{stdin_data.strip()}" if stdin_data else ""
        if context:
            full_prompt = (
                f"{system}\n\n{context}\n\n[Prompt]\n{user_prompt}\n\n[Response]"
            )
        else:
            full_prompt = f"{system}\n\n[Prompt]\n{user_prompt}\n\n[Response]"

        thinking_budget = 4096 if args.thinking else 0

        response_text = await asyncio.get_event_loop().run_in_executor(
            None, call_gemini, full_prompt, api_key, thinking_budget
        )
        print(response_text, end="", flush=True)
        if not response_text.endswith("\n"):
            print()
        return

    # --- DAEMON-CONNECTED MODE ---
    shell_pid = os.getppid()
    socket_path = get_socket_path(shell_pid)

    if not stdin_data and not user_prompt and not args.print_context and not args.clear:
        print(f"[Info] {INFO}")
        try:
            _, writer = await asyncio.open_unix_connection(socket_path)
            writer.close()
            await writer.wait_closed()
            print("[Info] Daemon running")
        except:
            print("[Info] Daemon not running")
        return

    # Connect or start daemon
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
    except:
        run_daemon(shell_pid)
        for _ in range(20):
            await asyncio.sleep(0.1)
            try:
                reader, writer = await asyncio.open_unix_connection(socket_path)
                break
            except:
                continue
        else:
            print("[Error] Daemon failed to start.", file=sys.stderr)
            return

    if args.clear:
        await send_json(writer, {"action": "clear"})
        await recv_json(reader)
    elif args.print_context:
        await send_json(writer, {"action": "get"})
        msg = await recv_json(reader)
        if msg:
            print(msg.get("data", "").strip() or "(empty)")

    if args.clear or args.print_context:
        writer.close()
        await writer.wait_closed()
        return

    await send_json(writer, {"action": "get"})
    msg = await recv_json(reader)
    context = msg.get("data", "") if msg else ""

    if stdin_data:
        if context:
            context += "\n\n"
        context += f"[Context]\n{stdin_data.strip()}"

    if not user_prompt:
        if stdin_data:
            await send_json(writer, {"action": "set", "data": context})
            await recv_json(reader)
            print("[Context Added]")
        writer.close()
        await writer.wait_closed()
        return

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[Error] Missing GEMINI_API_KEY", file=sys.stderr)
        writer.close()
        await writer.wait_closed()
        return

    system = SYSTEM_PROMPT
    l = (
        "short"
        if args.short
        else "medium"
        if args.medium
        else "long"
        if args.long
        else None
    )
    if l == "short":
        system += "\nRespond in 1-2 sentences."
    elif l == "medium":
        system += "\nRespond in 3-5 sentences."
    elif l == "long":
        system += "\nRespond in 2-3 paragraphs."

    if context:
        full_prompt = (
            f"{system}\n\n[Context]\n{context}\n\n[Prompt]\n{user_prompt}\n\n[Response]"
        )
    else:
        full_prompt = f"{system}\n\n[Prompt]\n{user_prompt}\n\n[Response]"
    thinking_budget = 4096 if args.thinking else 0

    response_text = await asyncio.get_event_loop().run_in_executor(
        None, call_gemini, full_prompt, api_key, thinking_budget
    )
    print(response_text, end="", flush=True)
    if not response_text.endswith("\n"):
        print()

    new_context = (
        context + f"\n\n[Prompt]\n{user_prompt}\n\n[Response]\n{response_text.strip()}"
    )
    await send_json(writer, {"action": "set", "data": new_context})
    await recv_json(reader)

    writer.close()
    await writer.wait_closed()


# ------------------- Daemon -------------------


async def daemon_handle(reader, writer, state: State):
    try:
        while not reader.at_eof():
            data = await recv_json(reader)
            if not data:
                break

            action = data.get("action")
            if action == "get":
                await send_json(writer, {"type": "context", "data": state.context})
            elif action == "set":
                state.context = data.get("data", "")
                await send_json(writer, {"status": "ok"})
            elif action == "clear":
                state.context = ""
                await send_json(writer, {"status": "ok"})

    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        if not writer.is_closing():
            writer.close()
            await writer.wait_closed()


async def check_shell_alive(shell_pid):
    """Exit daemon if the parent shell process dies."""
    while True:
        await asyncio.sleep(5)
        try:
            os.kill(shell_pid, 0)
        except ProcessLookupError:
            os._exit(0)


async def run_daemon_loop(shell_pid):
    if not shell_pid:
        os._exit(1)
    state = State(pid=shell_pid)
    socket_path = get_socket_path(shell_pid)

    def cleanup():
        if os.path.exists(socket_path):
            try:
                os.unlink(socket_path)
            except OSError:
                pass

    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))

    if os.path.exists(socket_path):
        os.unlink(socket_path)

    server = await asyncio.start_unix_server(
        lambda r, w: daemon_handle(r, w, state), path=socket_path
    )
    asyncio.create_task(check_shell_alive(shell_pid))
    async with server:
        await server.serve_forever()


# ------------------- Entrypoint -------------------


def main():
    if os.getenv(DAEMON_ENV) == "1":
        shell_pid = int(os.getenv("LLM_SHELL_PID", "0"))
        asyncio.run(run_daemon_loop(shell_pid))
    else:
        try:
            asyncio.run(run_client())
        except KeyboardInterrupt:
            print("\nInterrupted.")


if __name__ == "__main__":
    main()
