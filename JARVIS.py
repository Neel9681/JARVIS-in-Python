"""
Jarvis - simple desktop assistant using GPT-5
Usage: set environment variable OPENAI_API_KEY or paste your key into OPENAI_API_KEY variable.
Microphone listening -> query GPT-5 -> spoken reply -> optional PC control.

WARNING: This script runs system commands. Use responsibly.
"""

import os
import subprocess
import platform
import webbrowser
import time
import json
import threading
from queue import Queue

import openai
import speech_recognition as sr
import pyttsx3
import pyautogui
import wikipedia
from googletrans import Translator

# -----------------------------
# CONFIG
# -----------------------------
# Recommended: set OPENAI_API_KEY in your environment instead of pasting here.
OPENAI_API_KEY = os.getenv("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", "")  # or "sk-..." (not recommended)
if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY not found in environment. Please set it before using GPT functions.")
openai.api_key = OPENAI_API_KEY

# Model to use
GPT_MODEL = "gpt-5"  # change if your account exposes a different name

# Wake word (optional). If you prefer push-to-talk set WAKE_WORD = None and use input() loop.
WAKE_WORD = "jarvis"

# Languages: default reply language; you can change per request in conversation.
DEFAULT_LANGUAGE = "English"

# TTS engine init
engine = pyttsx3.init()
engine.setProperty("rate", 165)  # speaking speed

# Recognizer
recognizer = sr.Recognizer()
mic = sr.Microphone()

# A simple queue to avoid concurrent audio playback / recognition collisions
q = Queue()

translator = Translator()

# -----------------------------
# UTILITIES
# -----------------------------
def speak(text):
    """Speak text out loud (non-blocking)"""
    def _s():
        engine.say(text)
        engine.runAndWait()
    t = threading.Thread(target=_s, daemon=True)
    t.start()

def listen(timeout=5, phrase_time_limit=12):
    """Listen from the microphone and return recognized text (or None)."""
    with mic as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.4)
        try:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            txt = recognizer.recognize_google(audio)
            return txt
        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            return None
        except sr.RequestError:
            # This error means the default Google recognizer couldn't be reached.
            # You can configure an offline recognizer like VOSK for production.
            return None

def ask_gpt(prompt, system_prompt=None, language=None, max_tokens=800):
    """Send prompt to GPT-5 and return text response. Language hint is sent in the prompt."""
    if not openai.api_key:
        return "Error: OpenAI API key not set. Please set OPENAI_API_KEY."

    # Add small instruction to reply in language if provided
    language_instruction = ""
    if language and language.lower() != "english":
        language_instruction = f"\nPlease respond in {language}. Keep the response concise when appropriate."

    messages = []
    if system_prompt:
        messages.append({"role":"system","content":system_prompt})
    messages.append({"role":"user","content": prompt + language_instruction})

    try:
        resp = openai.ChatCompletion.create(
            model=GPT_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.4,
        )
        # ChatCompletion returns choices list
        text = resp.choices[0].message.content.strip()
        return text
    except Exception as e:
        return f"Error contacting OpenAI: {e}"

# -----------------------------
# SAFE COMMAND EXECUTION
# -----------------------------
def confirm_action(prompt):
    """Ask user for yes/no via voice or text fallback"""
    print(prompt + " (yes/no)")
    speak(prompt + " Say yes to confirm, no to cancel.")
    # Give user a short window to say yes/no
    ans = listen(timeout=4, phrase_time_limit=4)
    if ans:
        ans = ans.lower()
        if "yes" in ans or "yeah" in ans or "yup" in ans or "sure" in ans:
            return True
        else:
            return False
    else:
        # fallback to keyboard input
        try:
            inp = input("Confirm? (y/n): ").strip().lower()
            return inp.startswith("y")
        except Exception:
            return False

def run_shell_command(cmd, require_confirm=False):
    """Run a shell command. If require_confirm is True, ask user first."""
    dangerous_keywords = ["rm ", "del ", "format", "mkfs", "shutdown", "reboot", "poweroff", "erase"]
    if require_confirm or any(k in cmd.lower() for k in dangerous_keywords):
        ok = confirm_action(f"About to run: {cmd}. Confirm?")
        if not ok:
            return "Cancelled by user."
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        output = proc.stdout if proc.stdout else proc.stderr
        return output.strip()[:4000]  # limit length
    except Exception as e:
        return f"Command error: {e}"

# -----------------------------
# ACTIONS (apps, screenshot, open website)
# -----------------------------
def open_app(app_name):
    """Try to open an application by name (Windows/Mac/Linux heuristics)."""
    os_name = platform.system().lower()
    try:
        if os_name == "windows":
            subprocess.Popen(f'start "" "{app_name}"', shell=True)
        elif os_name == "darwin":
            subprocess.Popen(["open", "-a", app_name])
        else:  # linux
            subprocess.Popen([app_name])
        return f"Opening {app_name}."
    except Exception as e:
        return f"Failed to open {app_name}: {e}"

def close_app(process_name):
    """Close app by killing process name."""
    os_name = platform.system().lower()
    cmd = ""
    if os_name == "windows":
        cmd = f"taskkill /im {process_name} /f"
    else:
        cmd = f"pkill -f {process_name}"
    return run_shell_command(cmd, require_confirm=True)

def take_screenshot(save_path="screenshot.png"):
    """Take a screenshot and save it."""
    try:
        img = pyautogui.screenshot()
        img.save(save_path)
        return f"Screenshot saved to {os.path.abspath(save_path)}"
    except Exception as e:
        return f"Screenshot failed: {e}"

def open_website(url):
    """Open url in default browser."""
    try:
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url
        webbrowser.open(url)
        return f"Opened {url} in browser."
    except Exception as e:
        return f"Failed to open {url}: {e}"

# -----------------------------
# HIGH-LEVEL PARSING: interpret commands vs chit-chat
# -----------------------------
def handle_user_request(text, preferred_language=DEFAULT_LANGUAGE):
    """Decide if user asked to run a command or asked a question; call GPT if necessary."""
    text_lower = (text or "").lower().strip()
    if not text_lower:
        return "I didn't hear anything."

    # quick local handlers (faster than round-tripping to GPT)
    if text_lower.startswith("open "):
        app = text[5:].strip()
        return open_app(app)
    if text_lower.startswith("close ") or text_lower.startswith("kill "):
        proc = text.split(" ", 1)[1]
        return close_app(proc)
    if "screenshot" in text_lower or "take screenshot" in text_lower:
        return take_screenshot()
    if text_lower.startswith("search "):
        query = text.split(" ", 1)[1]
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        open_website(url)
        return f"Searching the web for {query}."
    if text_lower.startswith("open website ") or text_lower.startswith("open site "):
        url = text.split(" ", 2)[2] if len(text.split(" ", 2)) > 2 else ""
        return open_website(url)

    # system info requests
    if "time" in text_lower or "date" in text_lower:
        return time.strftime("Today is %A, %B %d, %Y. The time is %I:%M %p.", time.localtime())

    # wikipedia quick lookup
    if text_lower.startswith("who is ") or text_lower.startswith("what is ") or text_lower.startswith("tell me about "):
        topic = text.split(" ", 2)[2] if len(text.split(" ", 2)) > 2 else text
        try:
            summary = wikipedia.summary(topic, sentences=2)
            return summary
        except Exception:
            # fallback to GPT
            pass

    # otherwise, use GPT-5 for a conversational answer (optionally in preferred_language)
    system_prompt = (
        "You are Jarvis, a helpful desktop assistant. Keep answers concise and actionable. "
        "If the user asks to run a system command, respond with a JSON object with keys: action and command, "
        "where action is one of ['run_shell','open_website','open_app','close_app','none'] and command is the string. "
        "If the user simply asks a question, answer naturally."
    )

    # Ask GPT to either give an action or an answer.
    combined_prompt = (
        f"User: {text}\n\n"
        "If this is an instruction to control the computer (open an app, run a shell command, open a website, close an app), "
        "respond ONLY with a JSON object like {\"action\":\"run_shell\",\"command\":\"ls -la\"}. "
        "If this is a normal question or conversation, respond with the answer in plain text."
    )

    resp = ask_gpt(combined_prompt, system_prompt=system_prompt, language=preferred_language)
    # Try to detect JSON action
    resp_stripped = resp.strip()
    if resp_stripped.startswith("{") and "action" in resp_stripped:
        try:
            j = json.loads(resp_stripped)
            action = j.get("action")
            cmd = j.get("command", "")
            # map actions
            if action == "run_shell":
                return run_shell_command(cmd, require_confirm=True)
            elif action == "open_website":
                return open_website(cmd)
            elif action == "open_app":
                return open_app(cmd)
            elif action == "close_app":
                return close_app(cmd)
            else:
                return "I parsed an action but couldn't map it to a known command."
        except Exception as e:
            # Not valid JSON -> treat as text
            return resp
    else:
        return resp

# -----------------------------
# MAIN LOOP
# -----------------------------
def main_loop():
    speak("Jarvis is online. Say the wake word or press Enter to type.")
    while True:
        try:
            if WAKE_WORD:
                print(f"Say '{WAKE_WORD}' to wake Jarvis, or type 'type:' to type a message.")
                spoken = listen(timeout=6, phrase_time_limit=4)
                if not spoken:
                    # small sleep to avoid busy loop
                    time.sleep(0.6)
                    continue
                print("Heard:", spoken)
                if WAKE_WORD in spoken.lower():
                    speak("Yes?")
                    # actual command
                    cmd = listen(timeout=6, phrase_time_limit=12)
                    if not cmd:
                        speak("I didn't catch that.")
                        continue
                    print("Command:", cmd)
                    q.put(cmd)
                elif spoken.lower().startswith("type:"):
                    # user typed the message after saying `type:`
                    cmd = spoken.split("type:",1)[1].strip()
                    q.put(cmd)
                else:
                    # random non-wake speech — ignore
                    continue
            else:
                # push-to-talk or typed input mode
                raw = input("You: ")
                if raw.strip().lower() == "exit":
                    speak("Going offline. Bye.")
                    break
                q.put(raw.strip())

            # process queue
            while not q.empty():
                user_text = q.get()
                # language detection / change: if user says "speak in <language>" set preferred
                preferred_language = DEFAULT_LANGUAGE
                if user_text.lower().startswith("speak in "):
                    preferred_language = user_text.split(" ", 2)[2]
                    speak(f"Okay. I will speak in {preferred_language} now.")
                    continue

                # If user asked to "translate to <lang>" use translator+GPT
                if user_text.lower().startswith("translate "):
                    # format: "translate <text> to <language>"
                    try:
                        # attempt split
                        parts = user_text.split(" to ")
                        text_to_translate = parts[0].split(" ",1)[1]
                        target_lang = parts[1]
                        # use googletrans local translation (quick)
                        translated = translator.translate(text_to_translate, dest=target_lang).text
                        speak("Translation ready.")
                        print(translated)
                        continue
                    except Exception:
                        pass

                # Handle request
                result = handle_user_request(user_text, preferred_language=preferred_language)
                print("Jarvis:", result)
                speak(result)

        except KeyboardInterrupt:
            print("Interrupted. Exiting.")
            break

if __name__ == "__main__":
    main_loop()
