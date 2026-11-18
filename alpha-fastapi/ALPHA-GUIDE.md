# Alpha: How to Use It

Alpha is the organization's memory bot on Slack. It reads the conversations it is allowed to, removes repeats, keeps a list of the projects going on and who works on them, and answers questions about them with Claude on Amazon Bedrock.

---

## For everyone: asking Alpha

### Two ways to ask

| Where | How | What Alpha answers from |
|---|---|---|
| A direct message to Alpha | Open a DM with **Alpha** and type your question | Admins: every conversation Alpha has read. Everyone else: public channels only |
| A channel | Write **@Alpha** and your question | Public channels only, whoever asks (everyone in the channel reads the answer). Alpha replies in the thread |

Admins who want answers that use private chats and DMs must ask in a **direct message**, never in a channel.

### Questions you can ask

- How many projects are going on, and who is working on them?
- What is the status of the billing revamp?
- What is Ravi working on?
- Who is the lead on the Globex pilot?
- What was decided about the website launch last week?
- What did people say about the pricing page?
- What happened in the last 3 days?

Ask in plain words. Alpha looks up its list of projects and people, and searches the stored messages, then answers in a few seconds.

### Words Alpha understands in a direct message

| Type | What happens |
|---|---|
| `connect` | Alpha sends you a link to let it read your conversations (see below) |
| `disconnect` | Alpha stops reading your conversations |
| `help` | Alpha shows what it can do |

### How fresh the answers are

| Something new | When Alpha knows it |
|---|---|
| A message in a channel the bot is in | At once |
| A direct message, a group DM, a private channel | Within 2 minutes |
| The list of projects and people | Within about 10 minutes (Alpha also searches the messages themselves, so a new project is usually found sooner) |

---

## For everyone: letting Alpha read your conversations (signing in)

Slack never lets a bot read a direct message it is not part of. Each person allows it once:

1. In Slack, open a direct message with **Alpha** and send `connect`.
2. Open the link Alpha replies with.
3. Slack's own page lists what Alpha may read. Click **Allow**.
4. A page says **Connected**. Within about 2 minutes Alpha starts reading.

### What Alpha reads once you sign in

- Every channel you are in, public and private, archived ones too, with every person's messages in them.
- All your direct messages, with every person, both sides.
- Your group DMs.
- Thread replies in all of them.
- Files shared in them: PDF, Word, Excel and text are read; other files are noted by name.
- The last 90 days (Slack's Free plan keeps no more), then everything new as it happens.

### What it does not read

- Direct messages between two other people. Those are read only if one of them signs in.
- Your direct messages with Alpha itself (those are questions to the bot).

### Good to know

- A conversation is read once, however many of its people sign in. A DM between two people who both signed in is stored once.
- Your private chats are used only in answers to **admins** who ask in a **direct message**.
- `disconnect` stops further reading. What was already read stays in Alpha's memory.
- A message deleted in Slack after Alpha read it stays in Alpha's memory.

---

## For the person who runs Alpha

### One-time Slack app setup (api.slack.com/apps, then Alpha)

1. **Basic Information**: copy **Client ID** and **Client Secret** into `.env` as `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET`. Never paste them in a chat or commit them.
2. **OAuth & Permissions**, **User Token Scopes**: `channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `mpim:history`, `mpim:read`, `files:read`, `users:read`.
3. **OAuth & Permissions**, **Bot Token Scopes**: add `files:read`, `mpim:history`, `mpim:read` to what the bot already has.
4. **OAuth & Permissions**, **Redirect URLs**: add `<PUBLIC_URL>/api/v1/slack/oauth/callback`, then **Save URLs**.
5. **Event Subscriptions**: the bot events `app_mention`, `message.channels`, `message.groups`, `message.im`, `message.mpim`.
6. **Reinstall to Workspace**. If the bot token changes, update `SLACK_BOT_TOKEN` in `.env`.

### `.env`

Copy `.env.example` to `.env` and fill it in. The keys that matter:

| Key | What it is |
|---|---|
| `SLACK_APP_TOKEN`, `SLACK_BOT_TOKEN` | The bot (Socket Mode) |
| `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` | Each person's sign-in |
| `PUBLIC_URL` | The https address Slack sends people back to after Allow |
| `TOKEN_KEY` | Any long random string; encrypts people's sign-ins. Changing it means everyone signs in again |
| `API_KEY` | Sent as `X-API-Key` by admin REST callers |
| `ADMIN_USER_IDS` | Extra admins by Slack user ID, comma separated (Slack workspace admins and owners already count) |
| `AWS_PROFILE`, `AWS_REGION` | Alpha's AWS account for Bedrock (`alpha`, `us-east-1`) |
| `SLACK_SYNC_DAYS` | How far back the first read goes (90 on Slack's Free plan) |

### Running it

```bash
pip install -r requirements.txt
AWS_PROFILE=alpha uvicorn app.main:app --port 8000
```

Slack needs an https address for sign-in. Running on a laptop, a tunnel gives one:

```bash
cloudflared tunnel --url http://localhost:8000
```

Put the address it prints in `PUBLIC_URL`, add the Redirect URL in the Slack app, and restart Alpha. A quick tunnel's address changes every time it restarts; a fixed address needs a named tunnel or a server.

### Inviting everyone to sign in

```bash
curl -X POST -H "X-API-Key: $API_KEY" <PUBLIC_URL>/api/v1/slack/invite
```

Alpha sends each person who has not signed in a direct message with the link.

### Admin addresses (all need the `X-API-Key` header)

| Address | What it gives |
|---|---|
| `GET /api/v1/memory/status` | Messages, conversations by kind, people, people signed in, last read from Slack |
| `GET /api/v1/memory/projects` | The list of projects with their people |
| `GET /api/v1/memory/people` | What each person works on |
| `GET /api/v1/slack/connections` | Who has signed in, and whose sign-in stopped working |
| `POST /api/v1/slack/invite` | Sends the sign-in link to everyone not yet signed in |
| `POST /api/v1/memory/build` | Rebuilds the list of projects and people now |
| `POST /api/v1/sync` | Reads Slack now |
| `POST /api/v1/ask` with `{"question": "..."}` | Answers from everything with the key, public channels only without it |

### Where things are kept

`data/alpha.db` (SQLite) holds the messages, file text, the encrypted sign-ins, day summaries and the list of projects and people. It holds everyone's chats: keep it private, never commit it (it is in `.gitignore`).
