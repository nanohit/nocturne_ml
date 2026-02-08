#!/usr/bin/env python3
"""
Nocturne - Venice.ai Multi-Account Proxy Server

Multi-account proxy with automatic rotation.
Supports dynamic account addition via admin panel.
"""

import asyncio
import json
import uuid
import time
import base64
import os
from aiohttp import web, ClientSession, CookieJar
from typing import Optional, List, Dict
from dataclasses import dataclass, field


# ==============================================================================
# Configuration
# ==============================================================================

# Venice.ai accounts - add new accounts here, commit & push to deploy
# All accounts use the same password: London2006)
ACCOUNTS = [
    {"email": "franciscovangelderen@protostarbusinesssolutions.com", "password": "London2006)"},
    {"email": "oscarmoralesj225@mysticmossmurmur.store", "password": "London2006)"},
    {"email": "deankoko8gg@kieunam.site", "password": "London2006)"},
    {"email": "elizagouldhozp@arthiti.com", "password": "London2006)"},
    {"email": "patrickklaassenycqs@phiasauem.info", "password": "London2006)"},
    {"email": "edithsolerb2qf@pod365.net", "password": "London2006)"},
    {"email": "ameliaholdenskiw@aerionx25k.io.vn", "password": "London2006)"},
    {"email": "henryhampton7xq0@lanoixe.com", "password": "London2006)"},
    {"email": "theodorehernandezt9q@subjectcompany.net", "password": "London2006)"},
    {"email": "idajolyedw9@bainbridgegrp.com", "password": "London2006)"},
    {"email": "carlosmontiw2wk@webehere.net", "password": "London2006)"},
    {"email": "cecelianiccolaiz2dd@ziyacan.com", "password": "London2006)"},
    {"email": "eleanorfujiwaramtmj@enzon.space", "password": "London2006)"},
    {"email": "garrettmadiai15no@palmerstudio.net", "password": "London2006)"},
    {"email": "jefferycresci63ec@rangexpext.net", "password": "London2006)"},
    {"email": "chestersimmonsf4n7@naturescradle.net", "password": "London2006)"},
    {"email": "chadhopkinsjs4t@omnilog.site", "password": "London2006)"},
    {"email": "joecianfanellina7e@missionbulldogs.org", "password": "London2006)"},
    {"email": "rogermazzoniioem@wmslegalservices.com", "password": "London2006)"},
    {"email": "travismoulin3n3q@vinheduca.life", "password": "London2006)"},
    # Add new accounts below this line:
]

DEFAULT_MODEL = "zai-org-glm-4.7-flash"  # GLM 4.7 Flash - follows instructions better

SYSTEM_PROMPT = """You are Nocturne, an AI assistant by Alphy.
* If asked your name or identity, say you are Nocturne by Alphy. Do NOT introduce yourself unless asked.
* Match the user's language (e.g., reply in Russian if they write in Russian).
* Be accurate and concise. If uncertain, say so.
* Never claim to be another AI system."""

CLERK_BASE = "https://clerk.venice.ai/v1"
OUTERFACE_BASE = "https://outerface.venice.ai/api"


# ==============================================================================
# Account Management
# ==============================================================================

@dataclass
class Account:
    email: str
    password: str
    jwt: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    remaining: int = 10
    exhausted: bool = False
    http: Optional[ClientSession] = field(default=None, repr=False)


class AccountPool:
    def __init__(self, accounts: List[Dict[str, str]]):
        self.accounts = [Account(email=a['email'], password=a['password']) for a in accounts]
        self.current_index = 0
        self._lock = asyncio.Lock()

    async def _login(self, account: Account) -> bool:
        """Login account with its own HTTP session"""
        print(f"[Pool] Logging in {account.email[:25]}...")

        try:
            # Each account gets its own session with cookie jar
            if account.http:
                await account.http.close()

            jar = CookieJar(unsafe=True)
            account.http = ClientSession(
                cookie_jar=jar,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
            )

            # Get client
            await account.http.get(f"{CLERK_BASE}/client")

            # Sign in
            async with account.http.post(
                f"{CLERK_BASE}/client/sign_ins",
                data={"identifier": account.email},
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            ) as resp:
                data = await resp.json()
                sign_in_id = data.get('response', {}).get('id')

            if not sign_in_id:
                return False

            # Password
            async with account.http.post(
                f"{CLERK_BASE}/client/sign_ins/{sign_in_id}/attempt_first_factor",
                data={"strategy": "password", "password": account.password},
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            ) as resp:
                data = await resp.json()
                status = data.get('response', {}).get('status')

            if status != 'complete':
                return False

            account.session_id = data.get('response', {}).get('created_session_id')

            # Get JWT
            async with account.http.post(
                f"{CLERK_BASE}/client/sessions/{account.session_id}/tokens"
            ) as resp:
                data = await resp.json()
                account.jwt = data.get('jwt')

            if not account.jwt:
                return False

            # Extract user ID from JWT
            try:
                payload = account.jwt.split('.')[1]
                payload += '=' * (4 - len(payload) % 4)
                decoded = json.loads(base64.urlsafe_b64decode(payload))
                account.user_id = decoded.get('sub', '')
            except:
                account.user_id = str(uuid.uuid4())

            account.exhausted = False
            account.remaining = 10
            print(f"[Pool] Logged in {account.email[:25]}... OK")
            return True

        except Exception as e:
            print(f"[Pool] Login error {account.email[:20]}: {e}")
            return False

    async def get_account(self) -> Optional[Account]:
        """Get active account"""
        async with self._lock:
            for _ in range(len(self.accounts)):
                account = self.accounts[self.current_index]

                if account.exhausted:
                    self.current_index = (self.current_index + 1) % len(self.accounts)
                    continue

                if not account.jwt:
                    if not await self._login(account):
                        account.exhausted = True
                        self.current_index = (self.current_index + 1) % len(self.accounts)
                        continue

                return account

            return None

    def mark_exhausted(self, account: Account):
        account.exhausted = True
        account.remaining = 0
        self.current_index = (self.current_index + 1) % len(self.accounts)
        print(f"[Pool] {account.email[:20]}... exhausted, rotating")

    async def close(self):
        for account in self.accounts:
            if account.http:
                await account.http.close()

    def get_status(self) -> Dict:
        active = [a for a in self.accounts if not a.exhausted]
        return {
            "total_accounts": len(self.accounts),
            "active_accounts": len(active),
            "total_remaining": sum(a.remaining for a in active),
            "accounts": [
                {"email": a.email[:20] + "...", "remaining": a.remaining, "active": not a.exhausted}
                for a in self.accounts
            ]
        }


# ==============================================================================
# Chat Function
# ==============================================================================

async def do_chat(pool: AccountPool, message: str, model: str = None, history: list = None) -> tuple:
    """Send chat and return (response_text, account, error)"""
    if model is None:
        model = DEFAULT_MODEL

    for _ in range(len(pool.accounts)):
        account = await pool.get_account()
        if not account:
            return None, None, "All accounts exhausted"

        # Build prompt with history
        prompt = list(history) if history else []
        prompt.append({"role": "user", "content": message})

        payload = {
            "clientProcessingTime": 1,
            "conversationType": "text",
            "includeVeniceSystemPrompt": False,
            "isCharacter": False,
            "modelId": model,
            "prompt": prompt,
            "reasoning": False,
            "requestId": str(uuid.uuid4())[:7],
            "simpleMode": True,
            "systemPrompt": SYSTEM_PROMPT,
            "userId": account.user_id,
            "webEnabled": True,
            "webScrapeEnabled": False,
        }

        try:
            async with account.http.post(
                f"{OUTERFACE_BASE}/inference/chat",
                json=payload,
                headers={
                    "Authorization": f"Bearer {account.jwt}",
                    "Content-Type": "application/json",
                    "Origin": "https://venice.ai",
                    "Referer": "https://venice.ai/chat",
                },
            ) as resp:
                remaining = resp.headers.get('x-ratelimit-remaining')
                if remaining:
                    account.remaining = int(remaining)

                if resp.status == 429:
                    pool.mark_exhausted(account)
                    continue

                if resp.status != 200:
                    text = await resp.text()
                    return None, account, f"API error: {text}"

                # Parse response
                full_text = ""
                async for line in resp.content:
                    if line:
                        try:
                            obj = json.loads(line.decode('utf-8'))
                            if obj.get('kind') == 'content':
                                full_text += obj.get('content', '')
                        except:
                            pass

                return full_text, account, None

        except Exception as e:
            return None, account, str(e)

    return None, None, "All accounts exhausted"


# ==============================================================================
# HTTP Handlers
# ==============================================================================

async def handle_chat(request: web.Request) -> web.Response:
    pool: AccountPool = request.app['pool']

    try:
        data = await request.json()
    except:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    message = data.get('message', data.get('prompt', ''))
    model = data.get('model', DEFAULT_MODEL)
    history = data.get('history', [])

    if not message:
        return web.json_response({"error": "No message"}, status=400)

    response, account, error = await do_chat(pool, message, model, history)

    if error:
        return web.json_response({"error": error}, status=503 if "exhausted" in error else 500)

    return web.json_response({
        "response": response,
        "remaining": account.remaining if account else 0,
    })


async def handle_chat_stream(request: web.Request) -> web.StreamResponse:
    """Streaming chat endpoint with account rotation"""
    pool: AccountPool = request.app['pool']

    try:
        data = await request.json()
    except Exception as e:
        print(f"[Stream] JSON parse error: {e}")
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    message = data.get('message', '')
    model = data.get('model', DEFAULT_MODEL)
    history = data.get('history', [])

    if not message:
        return web.json_response({"error": "No message"}, status=400)

    # Build prompt with history
    prompt = list(history)
    prompt.append({"role": "user", "content": message})

    # Try accounts until one works
    for attempt in range(len(pool.accounts)):
        account = await pool.get_account()
        if not account:
            return web.json_response({"error": "All accounts exhausted"}, status=503)

        payload = {
            "clientProcessingTime": 1,
            "conversationType": "text",
            "includeVeniceSystemPrompt": False,
            "isCharacter": False,
            "modelId": model,
            "prompt": prompt,
            "reasoning": False,
            "requestId": str(uuid.uuid4())[:7],
            "simpleMode": True,
            "systemPrompt": SYSTEM_PROMPT,
            "userId": account.user_id,
            "webEnabled": True,
            "webScrapeEnabled": False,
        }

        try:
            async with account.http.post(
                f"{OUTERFACE_BASE}/inference/chat",
                json=payload,
                headers={
                    "Authorization": f"Bearer {account.jwt}",
                    "Content-Type": "application/json",
                    "Origin": "https://venice.ai",
                    "Referer": "https://venice.ai/chat",
                },
            ) as resp:
                if resp.status == 429:
                    pool.mark_exhausted(account)
                    continue  # Try next account

                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[Stream] Venice API error {resp.status}: {error_text[:200]}")
                    return web.json_response({"error": error_text}, status=resp.status)

                remaining = resp.headers.get('x-ratelimit-remaining')
                if remaining:
                    account.remaining = int(remaining)

                response = web.StreamResponse()
                response.content_type = 'text/event-stream'
                response.headers['Cache-Control'] = 'no-cache'
                response.headers['X-Remaining'] = str(account.remaining)
                await response.prepare(request)

                async for line in resp.content:
                    if line:
                        try:
                            obj = json.loads(line.decode('utf-8'))
                            if obj.get('kind') == 'content':
                                await response.write(f"data: {json.dumps({'content': obj['content']}, ensure_ascii=False)}\n\n".encode('utf-8'))
                        except:
                            pass

                await response.write(b"data: [DONE]\n\n")
                return response
        except Exception as e:
            print(f"[Stream] Error with account: {e}")
            continue  # Try next account

    return web.json_response({"error": "All accounts exhausted"}, status=503)


async def handle_status(request: web.Request) -> web.Response:
    return web.json_response(request.app['pool'].get_status())


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_add_account(request: web.Request) -> web.Response:
    """Admin endpoint to add a new Venice account"""
    try:
        data = await request.json()
    except:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    email = data.get('email', '').strip()
    password = data.get('password', 'London2006)')

    if not email:
        return web.json_response({"error": "Email required"}, status=400)

    pool: AccountPool = request.app['pool']

    # Check if account already exists
    for acc in pool.accounts:
        if acc.email == email:
            return web.json_response({"error": "Account already exists"}, status=400)

    # Add new account
    new_account = Account(email=email, password=password)
    pool.accounts.append(new_account)

    print(f"[Admin] Added account: {email}")
    return web.json_response({"success": True, "total_accounts": len(pool.accounts)})


async def handle_index(request: web.Request) -> web.Response:
    html = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Nocturne</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/11.1.1/marked.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/contrib/auto-render.min.js"></script>
    <style>
        :root {
            --bg: #1D2227;
            --bg-secondary: #24292F;
            --text-primary: #C7D3E1;
            --text-secondary: #8097B2;
            --text-white: #FFFFFF;
            --accent: #5B8DEF;
            --sidebar-width: 280px;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html, body { height: 100%; overflow: hidden; }
        body {
            font-family: 'Montserrat', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg);
            color: var(--text-primary);
            display: flex;
            height: 100vh;
            height: 100dvh;
        }

        /* Sidebar */
        .sidebar {
            width: var(--sidebar-width);
            background: var(--bg);
            border-right: 1px solid rgba(199,211,225,0.1);
            display: flex;
            flex-direction: column;
            position: fixed;
            left: 0; top: 0; bottom: 0;
            z-index: 100;
            transform: translateX(-100%);
            transition: transform 0.3s ease;
        }
        .sidebar.open { transform: translateX(0); }
        .sidebar-overlay {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.5);
            z-index: 99;
        }
        .sidebar-overlay.show { display: block; }

        .new-chat-btn {
            margin: 16px;
            padding: 14px;
            background: rgba(199,211,225,0.1);
            border: 1px solid rgba(199,211,225,0.2);
            border-radius: 8px;
            color: var(--text-primary);
            font-family: inherit;
            font-size: 28px;
            font-weight: 300;
            cursor: pointer;
            transition: all 0.2s;
            line-height: 1;
        }
        .new-chat-btn:hover { background: rgba(199,211,225,0.15); }

        .conversations-list {
            flex: 1;
            overflow-y: auto;
            padding: 0 16px;
        }
        .conv-section { margin-bottom: 20px; }
        .conv-section-title {
            font-size: 13px;
            color: var(--text-secondary);
            margin-bottom: 8px;
            padding-left: 4px;
        }
        .conv-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: rgba(199,211,225,0.05);
            border: 1px solid rgba(199,211,225,0.1);
            border-radius: 6px;
            margin-bottom: 4px;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
        }
        .conv-item::before {
            content: '';
            position: absolute;
            inset: -1px;
            border-radius: 6px;
            padding: 1px;
            background: linear-gradient(135deg, rgba(91,141,239,0.4), rgba(74,124,224,0.2));
            -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
            -webkit-mask-composite: xor;
            mask-composite: exclude;
            opacity: 0;
            transition: opacity 0.3s;
            pointer-events: none;
            z-index: 0;
        }
        .conv-item:hover {
            background: rgba(199,211,225,0.08);
            border-color: rgba(91,141,239,0.3);
            box-shadow: 0 2px 8px rgba(91,141,239,0.1);
        }
        .conv-item:hover::before { opacity: 1; }
        .conv-item.active {
            background: rgba(91,141,239,0.12);
            border-color: rgba(91,141,239,0.4);
            box-shadow: 0 0 0 1px rgba(91,141,239,0.2), 0 4px 12px rgba(91,141,239,0.15);
        }
        .conv-item.active::before { opacity: 1; }
        .conv-item-title {
            font-size: 13px;
            color: var(--text-primary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            flex: 1;
            position: relative;
            z-index: 1;
        }
        .conv-item-menu {
            color: var(--text-secondary);
            padding: 4px 6px;
            cursor: pointer;
            opacity: 0;
            transition: opacity 0.2s;
            font-size: 14px;
            position: relative;
            z-index: 2;
        }
        .conv-item:hover .conv-item-menu { opacity: 1; }

        .sidebar-footer {
            padding: 16px;
            border-top: 1px solid rgba(199,211,225,0.1);
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .admin-btn {
            font-size: 13px;
            color: var(--text-secondary);
            cursor: pointer;
            transition: color 0.2s;
            background: none;
            border: none;
            text-align: left;
            padding: 0;
            font-family: inherit;
        }
        .admin-btn:hover { color: var(--text-primary); }
        .admin-btn.hidden { display: none; }

        /* Main Content */
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            height: 100%;
            margin-left: 0;
            transition: margin-left 0.3s ease;
        }

        /* Header */
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 16px 20px;
            flex-shrink: 0;
        }
        .header-left {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .menu-btn {
            background: none;
            border: none;
            color: var(--text-primary);
            cursor: pointer;
            padding: 8px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .menu-icon {
            width: 24px;
            height: 18px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }
        .menu-icon span {
            display: block;
            height: 2px;
            background: var(--text-primary);
            border-radius: 1px;
        }
        .menu-icon span:first-child { width: 100%; }
        .menu-icon span:last-child { width: 70%; }
        .plus-btn {
            font-size: 28px;
            color: var(--text-primary);
            background: none;
            border: none;
            cursor: pointer;
            padding: 4px 8px;
            line-height: 1;
        }
        .header-right {
            font-size: 13px;
            color: var(--text-secondary);
        }

        /* Chat Area */
        .chat-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            scroll-behavior: smooth;
        }

        /* Welcome Screen */
        .welcome {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            justify-content: center;
            height: 100%;
            text-align: left;
            padding: 40px 20px;
            max-width: 800px;
            margin: 0 auto;
        }
        .welcome h1 {
            font-size: 26px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 8px;
        }
        .welcome p {
            font-size: 15px;
            color: var(--text-secondary);
            line-height: 1.5;
        }
        .welcome-actions {
            display: flex;
            gap: 10px;
            margin: 28px 0;
            flex-wrap: wrap;
            justify-content: flex-start;
        }
        .welcome-btn {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 16px;
            background: rgba(199,211,225,0.08);
            border: 1px solid rgba(199,211,225,0.2);
            border-radius: 8px;
            color: var(--text-primary);
            font-family: inherit;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .welcome-btn:hover { background: rgba(199,211,225,0.12); }
        .welcome-btn svg { width: 18px; height: 18px; }

        .suggestions {
            width: 100%;
        }
        .suggestion {
            text-align: left;
            padding: 14px 0;
            border-bottom: 1px solid rgba(199,211,225,0.1);
            color: var(--text-secondary);
            font-size: 15px;
            cursor: pointer;
            transition: color 0.2s;
        }
        .suggestion:hover { color: var(--text-primary); }
        .suggestion:last-child { border-bottom: none; }

        /* Messages */
        .message { margin-bottom: 24px; max-width: 650px; margin-left: 20px; }
        .message.user {
            display: flex;
            justify-content: flex-start;
            margin-left: 20px;
            margin-right: auto;
        }
        .message.user .message-content {
            background: rgba(91,141,239,0.15);
            border: 1px solid rgba(91,141,239,0.3);
            padding: 12px 16px;
            border-radius: 16px;
            border-bottom-right-radius: 4px;
            max-width: 85%;
            color: var(--text-white);
            font-size: 15px;
            line-height: 1.5;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }
        .message.assistant .message-content {
            color: var(--text-white);
            font-size: 15px;
            line-height: 1.7;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }

        /* Markdown Styles */
        .message.assistant .message-content h1,
        .message.assistant .message-content h2,
        .message.assistant .message-content h3,
        .message.assistant .message-content h4 {
            font-family: 'Montserrat', sans-serif;
            color: var(--text-white);
            margin: 20px 0 12px;
        }
        .message.assistant .message-content h1 { font-size: 22px; }
        .message.assistant .message-content h2 { font-size: 18px; }
        .message.assistant .message-content h3 { font-size: 16px; }
        .message.assistant .message-content p { margin: 12px 0; }
        .message.assistant .message-content ul,
        .message.assistant .message-content ol {
            margin: 12px 0;
            padding-left: 24px;
        }
        .message.assistant .message-content li { margin: 6px 0; }
        .message.assistant .message-content a {
            color: var(--accent);
            text-decoration: none;
        }
        .message.assistant .message-content a:hover { text-decoration: underline; }

        /* Code Blocks */
        .code-block {
            position: relative;
            margin: 16px 0;
            border-radius: 8px;
            overflow: hidden;
            background: #282c34;
        }
        .code-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            background: rgba(255,255,255,0.05);
            font-size: 12px;
            color: var(--text-secondary);
        }
        .copy-btn {
            background: none;
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            display: flex;
            align-items: center;
            gap: 4px;
            transition: all 0.2s;
        }
        .copy-btn:hover { background: rgba(255,255,255,0.1); color: var(--text-primary); }
        .code-block pre {
            margin: 0;
            padding: 16px;
            overflow-x: auto;
        }
        .code-block code {
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
            font-size: 13px;
            line-height: 1.5;
        }
        .message.assistant .message-content code:not(.hljs) {
            background: rgba(199,211,225,0.1);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 13px;
        }

        /* Input Area */
        .input-area {
            padding: 0 20px 0 20px;
            flex-shrink: 0;
        }
        .input-wrapper {
            display: flex;
            align-items: flex-end;
            background: var(--bg-secondary);
            border: 1px solid rgba(199,211,225,0.2);
            border-radius: 16px;
            border-bottom-left-radius: 0;
            border-bottom-right-radius: 0;
            border-bottom: none;
            padding: 14px;
            padding-bottom: 40px;
            gap: 12px;
            min-height: 70px;
            max-width: 800px;
            margin: 0 auto;
        }
        .input-wrapper textarea {
            flex: 1;
            background: none;
            border: none;
            color: var(--text-white);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            font-size: 16px;
            line-height: 1.4;
            resize: none;
            outline: none;
            max-height: 150px;
            min-height: 48px;
        }
        .input-wrapper textarea::placeholder { color: var(--text-secondary); }
        .send-btn {
            width: 44px;
            height: 44px;
            background: rgba(36,41,47,0.8);
            border: 1px solid rgba(128,151,178,0.2);
            border-radius: 10px;
            color: rgba(128,151,178,0.4);
            cursor: not-allowed;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            flex-shrink: 0;
        }
        .send-btn svg {
            width: 20px;
            height: 20px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            opacity: 0.4;
        }
        .send-btn.active {
            background: linear-gradient(135deg, var(--accent) 0%, #4A7CE0 100%);
            border-color: transparent;
            color: white;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(91,141,239,0.3);
        }
        .send-btn.active svg {
            opacity: 1;
            transform: translateY(-1px);
        }
        .send-btn.active:hover {
            background: linear-gradient(135deg, #4A7CE0 0%, var(--accent) 100%);
            box-shadow: 0 6px 16px rgba(91,141,239,0.4);
        }
        .send-btn.active:hover svg { transform: translateY(-2px); }
        .send-btn:disabled { opacity: 0.5; cursor: not-allowed; }

        /* Admin Panel */
        .admin-modal {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.7);
            z-index: 200;
            align-items: center;
            justify-content: center;
        }
        .admin-modal.show { display: flex; }
        .admin-panel {
            background: var(--bg-secondary);
            border-radius: 16px;
            padding: 24px;
            width: 90%;
            max-width: 400px;
            max-height: 80vh;
            overflow-y: auto;
        }
        .admin-panel h2 {
            font-size: 20px;
            margin-bottom: 20px;
            color: var(--text-primary);
        }
        .admin-panel input {
            width: 100%;
            padding: 12px 16px;
            background: var(--bg);
            border: 1px solid rgba(199,211,225,0.2);
            border-radius: 8px;
            color: var(--text-primary);
            font-size: 14px;
            margin-bottom: 12px;
            outline: none;
        }
        .admin-panel input:focus { border-color: var(--accent); }
        .admin-panel button {
            width: 100%;
            padding: 12px;
            background: var(--accent);
            border: none;
            border-radius: 8px;
            color: white;
            font-family: inherit;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 8px;
        }
        .admin-panel button:hover { opacity: 0.9; }
        .admin-panel .cancel-btn {
            background: rgba(199,211,225,0.1);
            color: var(--text-secondary);
        }
        .accounts-list {
            margin-top: 20px;
            max-height: 200px;
            overflow-y: auto;
        }
        .account-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px;
            background: var(--bg);
            border-radius: 6px;
            margin-bottom: 6px;
            font-size: 13px;
        }
        .account-email {
            color: var(--text-primary);
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .account-status {
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 4px;
        }
        .account-status.active { background: rgba(76,175,80,0.2); color: #4CAF50; }
        .account-status.exhausted { background: rgba(244,67,54,0.2); color: #F44336; }

        /* Context Menu */
        .context-menu {
            display: none;
            position: fixed;
            background: var(--bg-secondary);
            border: 1px solid rgba(199,211,225,0.2);
            border-radius: 8px;
            padding: 6px 0;
            z-index: 150;
            min-width: 150px;
        }
        .context-menu.show { display: block; }
        .context-menu-item {
            padding: 10px 16px;
            color: var(--text-primary);
            font-size: 14px;
            cursor: pointer;
            transition: background 0.2s;
        }
        .context-menu-item:hover { background: rgba(199,211,225,0.1); }
        .context-menu-item.danger { color: #F44336; }

        /* Typing indicator */
        .typing-indicator {
            display: flex;
            gap: 4px;
            padding: 8px 0;
        }
        .typing-indicator span {
            width: 8px;
            height: 8px;
            background: var(--text-secondary);
            border-radius: 50%;
            animation: typing 1.4s infinite;
        }
        .typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
        .typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
        @keyframes typing {
            0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
            30% { transform: translateY(-6px); opacity: 1; }
        }

        /* Weights Page */
        .weights-page {
            display: none;
            flex-direction: column;
            padding: 40px 20px;
            height: 100%;
            overflow-y: auto;
            max-width: 600px;
        }
        .weights-page.show { display: flex; }
        .weights-back {
            background: none;
            border: none;
            color: var(--text-primary);
            font-size: 28px;
            cursor: pointer;
            padding: 8px;
            margin-bottom: 20px;
            align-self: flex-start;
        }
        .weights-title {
            font-size: 28px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 16px;
        }
        .weights-desc {
            font-size: 15px;
            color: var(--text-secondary);
            line-height: 1.6;
            margin-bottom: 24px;
        }
        .magnet-box {
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 16px;
            position: relative;
            margin-bottom: 24px;
        }
        .magnet-box code {
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 13px;
            color: var(--text-primary);
            word-break: break-all;
            line-height: 1.6;
            display: block;
        }
        .magnet-copy {
            position: absolute;
            top: 12px;
            right: 12px;
            background: none;
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            padding: 4px;
        }
        .magnet-copy:hover { color: var(--text-primary); }
        .weights-info {
            font-size: 14px;
            color: var(--text-secondary);
        }
        .weights-info strong {
            color: var(--text-primary);
        }

        /* Desktop */
        @media (min-width: 768px) {
            .sidebar { transform: translateX(0); }
            .sidebar.hidden { transform: translateX(-100%); }
            .sidebar-overlay { display: none !important; }
            .main { margin-left: var(--sidebar-width); transition: margin-left 0.3s ease; }
            .main.sidebar-hidden { margin-left: 0; }
            .chat-messages { padding: 40px 80px; }
            .input-area { padding: 0 80px 0 80px; }
            .input-wrapper { max-width: 900px; }
            .welcome { padding: 40px 80px; }
            .weights-page { padding: 40px 80px; }
            .welcome h1 { font-size: 32px; }
            .message { margin-left: 40px; }
            .message.user { margin-left: 40px; }
        }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(199,211,225,0.2); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(199,211,225,0.3); }
    </style>
</head>
<body>
    <!-- Sidebar Overlay -->
    <div class="sidebar-overlay" id="sidebarOverlay"></div>

    <!-- Sidebar -->
    <aside class="sidebar" id="sidebar">
        <button class="new-chat-btn" id="newChatBtn">+</button>
        <div class="conversations-list" id="conversationsList"></div>
        <div class="sidebar-footer">
            <button class="admin-btn hidden" id="accountsBtn">аккаунты</button>
            <button class="admin-btn" id="adminEntry">admin entry</button>
        </div>
    </aside>

    <!-- Main Content -->
    <main class="main">
        <header class="header">
            <div class="header-left">
                <button class="menu-btn" id="menuBtn">
                    <div class="menu-icon">
                        <span></span>
                        <span></span>
                    </div>
                </button>
                <button class="plus-btn" id="plusBtn">+</button>
            </div>
            <div class="header-right">by nanohit</div>
        </header>

        <div class="chat-container">
            <div class="chat-messages" id="chatMessages">
                <div class="welcome" id="welcomeScreen">
                    <h1>Nocturne-12B</h1>
                    <p>компактная, универсальная языковая модель без цензуры</p>
                    <div class="welcome-actions">
                        <button class="welcome-btn" id="weightsBtn">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <circle cx="12" cy="12" r="10"/>
                                <path d="M12 6v6l4 2"/>
                            </svg>
                            Веса
                        </button>
                        <button class="welcome-btn">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M12 20V10M18 20V4M6 20v-4"/>
                            </svg>
                            Бенчмарки
                        </button>
                        <button class="welcome-btn" onclick="window.open('https://huggingface.co/nanohit/nocturne','_blank')">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                <polyline points="14 2 14 8 20 8"/>
                            </svg>
                            Доки
                        </button>
                    </div>
                    <div class="suggestions">
                        <div class="suggestion" onclick="useSuggestion(this)">Обьясни политическую философию Платона.</div>
                        <div class="suggestion" onclick="useSuggestion(this)">Напиши итератор, генерирующий числа Фибоначчи типа u64 на Rust.</div>
                        <div class="suggestion" onclick="useSuggestion(this)">Как формируются чёрные дыры?</div>
                        <div class="suggestion" onclick="useSuggestion(this)">В чём смысл жизни?</div>
                    </div>
                </div>
            </div>

            <!-- Weights Page -->
            <div class="weights-page" id="weightsPage">
                <button class="weights-back" id="weightsBack">&larr;</button>
                <h1 class="weights-title">Веса</h1>
                <p class="weights-desc">Веса Nocturne-12B можно скачать через торрент по magnet-ссылке:</p>
                <div class="magnet-box">
                    <code id="magnetLink">magnet:?xt=urn:btih:93D846ECE06C32B757892E7C50A1C1B0F98C913B&tr=http%3A%2F%2Fbt2.t-ru.org%2Fann%3Fmagnet&dn=SolidWorks%202024%20SP5.0%20Full%20Premium%20Multilanguage%20x64%20%5B2024%2C%20Multi%20%2B%20RUS%5D</code>
                    <button class="magnet-copy" onclick="copyMagnet()">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                        </svg>
                    </button>
                </div>
                <div class="weights-info">
                    <p>размер <strong>16.8 GB</strong></p>
                    <p>место на диске <strong>20.7 GB</strong></p>
                </div>
            </div>

            <div class="input-area">
                <div class="input-wrapper">
                    <textarea id="messageInput" placeholder="Спросить у Nocturne...." rows="1"></textarea>
                    <button class="send-btn" id="sendBtn">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M12 19V5M5 12l7-7 7 7"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    </main>

    <!-- Admin Modal -->
    <div class="admin-modal" id="adminModal">
        <div class="admin-panel" id="adminPanel">
            <!-- Content injected by JS -->
        </div>
    </div>

    <!-- Context Menu -->
    <div class="context-menu" id="contextMenu">
        <div class="context-menu-item" data-action="rename">Переименовать</div>
        <div class="context-menu-item danger" data-action="delete">Удалить</div>
    </div>

    <script>
    // ============== State ==============
    const state = {
        conversations: JSON.parse(localStorage.getItem('nocturne_conversations') || '[]'),
        currentConvId: null,
        isAdmin: localStorage.getItem('nocturne_admin') === 'true',
        isStreaming: false,
        currentPage: 'chat'
    };

    // ============== DOM Elements ==============
    const $ = id => document.getElementById(id);
    const sidebar = $('sidebar');
    const sidebarOverlay = $('sidebarOverlay');
    const menuBtn = $('menuBtn');
    const plusBtn = $('plusBtn');
    const newChatBtn = $('newChatBtn');
    const adminEntry = $('adminEntry');
    const accountsBtn = $('accountsBtn');
    const adminModal = $('adminModal');
    const adminPanel = $('adminPanel');
    const chatMessages = $('chatMessages');
    const welcomeScreen = $('welcomeScreen');
    const weightsPage = $('weightsPage');
    const messageInput = $('messageInput');
    const sendBtn = $('sendBtn');
    const conversationsList = $('conversationsList');
    const contextMenu = $('contextMenu');

    // ============== Sidebar ==============
    function toggleSidebar() {
        const isDesktop = window.innerWidth >= 768;

        if (isDesktop) {
            // Desktop: toggle hidden class
            sidebar.classList.toggle('hidden');
            document.querySelector('.main').classList.toggle('sidebar-hidden');
        } else {
            // Mobile: toggle open class and overlay
            sidebar.classList.toggle('open');
            sidebarOverlay.classList.toggle('show');
        }
    }

    menuBtn.onclick = toggleSidebar;
    sidebarOverlay.onclick = toggleSidebar;

    // ============== Send Button Highlight ==============
    function updateSendButton() {
        if (messageInput.value.trim()) {
            sendBtn.classList.add('active');
        } else {
            sendBtn.classList.remove('active');
        }
    }
    messageInput.addEventListener('input', updateSendButton);

    // ============== Weights Page ==============
    window.showWeightsPage = function() {
        chatMessages.style.display = 'none';
        weightsPage.classList.add('show');
        state.currentPage = 'weights';
    };

    window.hideWeightsPage = function() {
        weightsPage.classList.remove('show');
        chatMessages.style.display = 'block';
        state.currentPage = 'chat';
    };

    // Attach to weights button after DOM ready
    document.addEventListener('click', (e) => {
        if (e.target.closest('#weightsBtn') || e.target.closest('[onclick*="weightsBtn"]')) {
            showWeightsPage();
        }
    });

    $('weightsBack').onclick = hideWeightsPage;

    // Re-attach weights button handler when welcome screen is cloned
    const originalRenderChat = renderChat;

    window.copyMagnet = function() {
        const magnet = $('magnetLink').textContent;
        navigator.clipboard.writeText(magnet).then(() => {
            const btn = document.querySelector('.magnet-copy');
            btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
            setTimeout(() => {
                btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
            }, 2000);
        });
    };

    // ============== Conversations ==============
    function saveConversations() {
        localStorage.setItem('nocturne_conversations', JSON.stringify(state.conversations));
    }

    function createConversation() {
        // Don't create if current conversation is empty
        if (state.currentConvId) {
            const currentConv = state.conversations.find(c => c.id === state.currentConvId);
            if (currentConv && currentConv.messages.length === 0) {
                // Current conversation is empty, just stay on it
                if (window.innerWidth < 768) toggleSidebar();
                messageInput.focus();
                return;
            }
        }

        // Clear current conversation ID to show welcome screen
        state.currentConvId = null;
        renderChat();
        renderConversationsList();
        if (window.innerWidth < 768) toggleSidebar();
        messageInput.focus();
    }

    function selectConversation(id) {
        state.currentConvId = id;
        renderChat();
        renderConversationsList();
    }

    function deleteConversation(id) {
        state.conversations = state.conversations.filter(c => c.id !== id);
        saveConversations();
        if (state.currentConvId === id) {
            state.currentConvId = null;
            renderChat();
        }
        renderConversationsList();
    }

    function renameConversation(id) {
        const conv = state.conversations.find(c => c.id === id);
        if (!conv) return;
        const newTitle = prompt('Название чата:', conv.title);
        if (newTitle && newTitle.trim()) {
            conv.title = newTitle.trim();
            saveConversations();
            renderConversationsList();
        }
    }

    function renderConversationsList() {
        // Filter out empty conversations except current one
        const validConvs = state.conversations.filter(c => c.messages.length > 0 || c.id === state.currentConvId);

        const today = new Date();
        today.setHours(0,0,0,0);
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);

        const todayConvs = validConvs.filter(c => new Date(c.createdAt) >= today);
        const yesterdayConvs = validConvs.filter(c => {
            const d = new Date(c.createdAt);
            return d >= yesterday && d < today;
        });
        const olderConvs = validConvs.filter(c => new Date(c.createdAt) < yesterday);

        let html = '';

        if (todayConvs.length) {
            html += '<div class="conv-section"><div class="conv-section-title">Сегодня</div>';
            todayConvs.forEach(c => html += renderConvItem(c));
            html += '</div>';
        }
        if (yesterdayConvs.length) {
            html += '<div class="conv-section"><div class="conv-section-title">Вчера</div>';
            yesterdayConvs.forEach(c => html += renderConvItem(c));
            html += '</div>';
        }
        if (olderConvs.length) {
            html += '<div class="conv-section"><div class="conv-section-title">Ранее</div>';
            olderConvs.forEach(c => html += renderConvItem(c));
            html += '</div>';
        }

        conversationsList.innerHTML = html;

        // Add click handlers
        conversationsList.querySelectorAll('.conv-item').forEach(el => {
            el.onclick = (e) => {
                if (!e.target.classList.contains('conv-item-menu')) {
                    selectConversation(el.dataset.id);
                    if (window.innerWidth < 768) toggleSidebar();
                }
            };
        });

        conversationsList.querySelectorAll('.conv-item-menu').forEach(el => {
            el.onclick = (e) => {
                e.stopPropagation();
                showContextMenu(e, el.closest('.conv-item').dataset.id);
            };
        });
    }

    function renderConvItem(conv) {
        const active = conv.id === state.currentConvId ? 'active' : '';
        const title = conv.title.length > 25 ? conv.title.slice(0,25) + '...' : conv.title;
        return `<div class="conv-item ${active}" data-id="${conv.id}">
            <span class="conv-item-title">${escapeHtml(title)}</span>
            <span class="conv-item-menu">•••</span>
        </div>`;
    }

    // ============== Context Menu ==============
    let contextMenuConvId = null;

    function showContextMenu(e, convId) {
        contextMenuConvId = convId;
        contextMenu.style.left = e.clientX + 'px';
        contextMenu.style.top = e.clientY + 'px';
        contextMenu.classList.add('show');
    }

    document.onclick = () => contextMenu.classList.remove('show');

    contextMenu.querySelectorAll('.context-menu-item').forEach(el => {
        el.onclick = () => {
            const action = el.dataset.action;
            if (action === 'delete') deleteConversation(contextMenuConvId);
            if (action === 'rename') renameConversation(contextMenuConvId);
        };
    });

    // ============== Chat ==============
    function renderChat() {
        // Hide weights page if showing
        hideWeightsPage();

        if (!state.currentConvId) {
            chatMessages.innerHTML = '';
            const clone = welcomeScreen.cloneNode(true);
            clone.style.display = 'flex';
            chatMessages.appendChild(clone);

            // Re-attach weights button handler
            const wb = clone.querySelector('#weightsBtn');
            if (wb) wb.onclick = showWeightsPage;

            return;
        }

        const conv = state.conversations.find(c => c.id === state.currentConvId);
        if (!conv) return;

        chatMessages.innerHTML = '';
        conv.messages.forEach(msg => {
            addMessageToDOM(msg.role, msg.content, false);
        });
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function addMessageToDOM(role, content, scroll = true) {
        const div = document.createElement('div');
        div.className = 'message ' + (role === 'user' ? 'user' : 'assistant');

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';

        if (role === 'user') {
            contentDiv.textContent = content;
        } else {
            contentDiv.innerHTML = renderMarkdown(content);
        }

        div.appendChild(contentDiv);
        chatMessages.appendChild(div);

        if (scroll) chatMessages.scrollTop = chatMessages.scrollHeight;
        return contentDiv;
    }

    function renderMarkdown(text) {
        // Process LaTeX - display mode ($$...$$)
        text = text.replace(/\$\$([^$]+)\$\$/g, (_, tex) => {
            try {
                return katex.renderToString(tex.trim(), { displayMode: true, throwOnError: false });
            } catch { return '$$' + tex + '$$'; }
        });

        // Process LaTeX - inline mode ($...$)
        text = text.replace(/\$([^$]+)\$/g, (_, tex) => {
            try {
                return katex.renderToString(tex.trim(), { throwOnError: false });
            } catch { return '$' + tex + '$'; }
        });

        // Also support \(...\) and \[...\] syntax
        text = text.replace(/\\\((.+?)\\\)/g, (_, tex) => {
            try {
                return katex.renderToString(tex, { throwOnError: false });
            } catch { return tex; }
        });
        text = text.replace(/\\\[(.+?)\\\]/gs, (_, tex) => {
            try {
                return katex.renderToString(tex, { displayMode: true, throwOnError: false });
            } catch { return tex; }
        });

        // Configure marked
        marked.setOptions({
            highlight: (code, lang) => {
                if (lang && hljs.getLanguage(lang)) {
                    return hljs.highlight(code, { language: lang }).value;
                }
                return hljs.highlightAuto(code).value;
            },
            breaks: true,
            gfm: true
        });

        // Custom renderer for code blocks
        const renderer = new marked.Renderer();
        renderer.code = (code, lang) => {
            const highlighted = lang && hljs.getLanguage(lang)
                ? hljs.highlight(code, { language: lang }).value
                : hljs.highlightAuto(code).value;
            const langLabel = lang || 'code';
            return `<div class="code-block">
                <div class="code-header">
                    <span>${langLabel}</span>
                    <button class="copy-btn" onclick="copyCode(this)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                        </svg>
                        Copy
                    </button>
                </div>
                <pre><code class="hljs">${highlighted}</code></pre>
            </div>`;
        };

        return marked.parse(text, { renderer });
    }

    window.copyCode = function(btn) {
        const code = btn.closest('.code-block').querySelector('code').textContent;
        navigator.clipboard.writeText(code).then(() => {
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg> Copied';
            setTimeout(() => {
                btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy';
            }, 2000);
        });
    };

    // ============== Send Message ==============
    async function sendMessage() {
        const text = messageInput.value.trim();
        if (!text || state.isStreaming) return;

        // Clean up empty conversations first
        state.conversations = state.conversations.filter(c => c.messages.length > 0 || c.id === state.currentConvId);

        // Create conversation if needed
        if (!state.currentConvId) {
            const conv = {
                id: Date.now().toString(),
                title: text.slice(0, 50),
                messages: [],
                createdAt: Date.now()
            };
            state.conversations.unshift(conv);
            state.currentConvId = conv.id;
            saveConversations();
            renderConversationsList();
        }

        const conv = state.conversations.find(c => c.id === state.currentConvId);

        // Clear welcome screen
        const welcome = chatMessages.querySelector('.welcome');
        if (welcome) welcome.remove();

        // Add user message
        conv.messages.push({ role: 'user', content: text });
        addMessageToDOM('user', text);
        messageInput.value = '';
        autoResizeInput();
        updateSendButton();

        // Update title if first message
        if (conv.messages.length === 1) {
            conv.title = text.slice(0, 50);
            renderConversationsList();
        }

        // Add typing indicator
        const aiDiv = document.createElement('div');
        aiDiv.className = 'message assistant';
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        contentDiv.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
        aiDiv.appendChild(contentDiv);
        chatMessages.appendChild(aiDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;

        state.isStreaming = true;
        sendBtn.disabled = true;

        try {
            // Build history for API
            const history = conv.messages.slice(0, -1).map(m => ({
                role: m.role === 'user' ? 'user' : 'assistant',
                content: m.content
            }));

            const resp = await fetch('/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, history })
            });

            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let fullText = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                for (const line of chunk.split('\\n')) {
                    if (line.startsWith('data: ') && line !== 'data: [DONE]') {
                        try {
                            const data = JSON.parse(line.slice(6));
                            if (data.content) {
                                fullText += data.content;
                                contentDiv.innerHTML = renderMarkdown(fullText);
                                chatMessages.scrollTop = chatMessages.scrollHeight;
                            }
                        } catch {}
                    }
                }
            }

            // Save assistant message
            conv.messages.push({ role: 'assistant', content: fullText });
            saveConversations();

        } catch (e) {
            contentDiv.innerHTML = '<span style="color:#F44336">Ошибка: ' + e.message + '</span>';
        }

        state.isStreaming = false;
        sendBtn.disabled = false;
        messageInput.focus();
    }

    sendBtn.onclick = () => {
        if (sendBtn.classList.contains('active')) sendMessage();
    };
    messageInput.onkeydown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    // Auto-resize textarea
    function autoResizeInput() {
        messageInput.style.height = 'auto';
        messageInput.style.height = Math.min(messageInput.scrollHeight, 150) + 'px';
        updateSendButton();
    }
    messageInput.oninput = autoResizeInput;

    // ============== Suggestions ==============
    window.useSuggestion = function(el) {
        messageInput.value = el.textContent;
        autoResizeInput();
        updateSendButton();
        messageInput.focus();
    };

    // ============== New Chat ==============
    newChatBtn.onclick = createConversation;
    plusBtn.onclick = createConversation;

    // ============== Admin ==============
    function updateAdminEntry() {
        adminEntry.textContent = state.isAdmin ? 'выйти' : 'admin entry';
        if (state.isAdmin) {
            accountsBtn.classList.remove('hidden');
        } else {
            accountsBtn.classList.add('hidden');
        }
    }

    accountsBtn.onclick = showAdminPanel;

    function showLoginForm() {
        adminPanel.innerHTML = `
            <h2>Вход администратора</h2>
            <input type="text" id="adminUser" placeholder="Имя пользователя">
            <input type="password" id="adminPass" placeholder="Пароль">
            <button onclick="attemptLogin()">Войти</button>
            <button class="cancel-btn" onclick="closeAdmin()">Отмена</button>
        `;
        adminModal.classList.add('show');
    }

    function showAdminPanel() {
        adminPanel.innerHTML = `
            <h2>Панель администратора</h2>
            <h3 style="font-size:14px;color:var(--text-secondary);margin-bottom:12px;">Добавить аккаунт</h3>
            <input type="email" id="newAccountEmail" placeholder="Email аккаунта Venice.ai">
            <button onclick="addAccount()">Добавить</button>
            <div class="accounts-list" id="accountsList">Загрузка...</div>
            <button class="cancel-btn" onclick="closeAdmin()" style="margin-top:16px;">Закрыть</button>
        `;
        adminModal.classList.add('show');
        loadAccounts();
    }

    async function loadAccounts() {
        try {
            const resp = await fetch('/status');
            const data = await resp.json();
            const list = $('accountsList');
            list.innerHTML = data.accounts.map(a => `
                <div class="account-item">
                    <span class="account-email">${a.email}</span>
                    <span class="account-status ${a.active ? 'active' : 'exhausted'}">
                        ${a.active ? a.remaining + ' left' : 'exhausted'}
                    </span>
                </div>
            `).join('');
        } catch (e) {
            $('accountsList').innerHTML = '<div style="color:#F44336">Ошибка загрузки</div>';
        }
    }

    window.attemptLogin = function() {
        const user = $('adminUser').value;
        const pass = $('adminPass').value;
        if (user === 'admin' && pass === 'London2006)') {
            state.isAdmin = true;
            localStorage.setItem('nocturne_admin', 'true');
            updateAdminEntry();
            closeAdmin();
            showAdminPanel();
        } else {
            alert('Неверные учетные данные');
        }
    };

    window.addAccount = async function() {
        const email = $('newAccountEmail').value.trim();
        if (!email) return;

        try {
            const resp = await fetch('/admin/add-account', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password: 'London2006)' })
            });
            if (resp.ok) {
                $('newAccountEmail').value = '';
                loadAccounts();
            } else {
                alert('Ошибка добавления аккаунта');
            }
        } catch (e) {
            alert('Ошибка: ' + e.message);
        }
    };

    window.closeAdmin = function() {
        adminModal.classList.remove('show');
    };

    adminEntry.onclick = () => {
        if (state.isAdmin) {
            state.isAdmin = false;
            localStorage.removeItem('nocturne_admin');
            updateAdminEntry();
        } else {
            showLoginForm();
        }
    };

    adminModal.onclick = (e) => {
        if (e.target === adminModal) closeAdmin();
    };

    // ============== Utils ==============
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ============== Init ==============
    updateAdminEntry();
    renderConversationsList();
    renderChat();
    </script>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html')


# ==============================================================================
# App
# ==============================================================================

async def on_startup(app):
    app['pool'] = AccountPool(ACCOUNTS)

async def on_cleanup(app):
    await app['pool'].close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()

    # Render.com and other PaaS use PORT env var
    port = args.port or int(os.environ.get('PORT', 8080))
    host = args.host

    print("=" * 60)
    print("Nocturne - Venice.ai Proxy Server")
    print(f"Accounts: {len(ACCOUNTS)} | Prompts/day: ~{len(ACCOUNTS) * 10}")
    print(f"Listening on: {host}:{port}")
    print("=" * 60)

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    app.router.add_get('/', handle_index)
    app.router.add_post('/chat', handle_chat)
    app.router.add_post('/stream', handle_chat_stream)
    app.router.add_get('/status', handle_status)
    app.router.add_get('/health', handle_health)
    app.router.add_post('/admin/add-account', handle_add_account)

    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    main()
