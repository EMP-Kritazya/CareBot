# Twilio: real phone calls

CareBot alerts people with **voice calls** (texts need a US registration that can take days, so
calls come first). Code: `carebot/escalate/twilio_calls.py` (placing calls) and
`carebot/escalate/twilio_hooks.py` (answers coming back).

| Who | What they hear | What they can do |
| --- | --- | --- |
| Krit (neighbor, `NEIGHBOR_PHONE`) | "CareBot: Sandy may have fallen and didn't answer CareBot's check-in. West 115th Street, apartment 4A…" | **Press 1** = "I can go" (Sandy hears "Krit from 115th Street is on the way"), **2** = can't |
| Suyog (family, `FAMILY_PHONE`) | The alert, then one call per update ("someone at the device said: 'we're taking him to the hospital'") | Listen |
| 911 stand-in (`PSAP_STANDIN_PHONE`, a teammate, **never real 911**) | The briefing: "This is CareBot, an automated home safety device, calling about Sandy, 81…" | **Ask questions out loud**; CareBot answers from the facts only ("Is he breathing?" gets "I don't know…") |

## 1. Upgrade the account (once)

The free trial can't speak CareBot's words or use your own number.
1. Before paying, ask the MakeCU organizers whether there's a Twilio promo code.
2. Console → **Upgrade** → add about $20. Calls cost about $0.014 a minute, so a whole demo day costs cents.
3. **Phone Numbers → Buy a number:** a US local number with **Voice**, about $1.15 a month.
4. In `.env`, set `TWILIO_FROM_NUMBER=+1XXXXXXXXXX` to the number you bought. Your friends should save it as "CareBot".

## 2. Check it, without calling anyone

```bash
.venv/bin/python -m apps.watcher --webcam 1 --twilio-dry-run --demo
```
Trigger an alert (`t`) and stay silent. The terminal prints every call (who, and exactly what they'd hear), and nobody is called.
Then run with `--twilio`. If anything is missing (still a trial, the number not owned, an empty phone in `.env`), CareBot
prints what's wrong and falls back to the console.

## 3. Answers coming back: the tunnel (optional, but needed for "press 1" and 911 questions)

Twilio has to reach your laptop. A free Cloudflare quick tunnel does it with no account:
```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8080
```
It prints a URL like `https://random-words.trycloudflare.com`. Put it in `.env`:
```
PUBLIC_BASE_URL=https://random-words.trycloudflare.com
```
Keep that terminal open. The URL changes every time you restart the tunnel, so update `.env` when it does.
Every webhook request must carry Twilio's signature; anything else gets 403.

## 4. Run for real

```bash
.venv/bin/python -m apps.watcher --webcam 1 --demo --twilio
```
At startup you'll see `Twilio: real calls. Answers come back via https://… -> localhost:8080`.
In the terminal, the calls show as `-> calling Krit (neighbor)`, key presses as `<- Krit pressed 1: YES`,
questions as `<- 911 asked: …` / `-> CareBot: …`, and call results as `<- call to 911: answered`.

CareBot never hangs up on the 911 call: when the dispatcher is quiet it keeps listening, says "CareBot is
still on the line" about every 30 s, and only hangs up after about 20 minutes of silence. Each update
(e.g. Krit's "we're taking him to the hospital") is spoken into that open call; if the dispatcher
already hung up, CareBot calls again, and that call stays open for questions too.

On the robot, run the tunnel on the Pi (`sudo apt install cloudflared`, or the .deb from Cloudflare) and add
`--twilio` to the watcher command.

## Texts: WhatsApp (today) or SMS (after registration)

Ordinary SMS to US numbers needs **A2P 10DLC registration** (Console → Messaging → Regulatory
compliance; days). Until then carriers block it (error 30034). So CareBot sends its texts by
**WhatsApp** (`twilio.texts: whatsapp` in `config/*.yaml`). Calls stay normal phone calls: Twilio
can't place WhatsApp calls to US numbers.

1. Console → Messaging → Try it out → **Send a WhatsApp message**. It shows the sandbox number
   (+1 415 523 8886) and a join code like `join two-words`.
2. On the FAMILY_PHONE (WhatsApp installed, same number as in `.env`), send `join two-words` to
   +1 415 523 8886. WhatsApp replies that you joined.
3. Nothing else in `.env` is needed (`TWILIO_WHATSAPP_FROM` is only for your own WhatsApp sender).
4. Test: `python -m apps.live_incident --public-url <tunnel> --texts whatsapp`.

Sandbox rules, so the demo doesn't fail:
- Joining lasts **3 days**. After that, send the join code again.
- CareBot's texts are free-form, so they only arrive within **24 hours of the last message that phone
  sent to the sandbox**. Before filming, send "hi" from the family phone. Error 63016 means the
  24 hours ran out; 63015 means the phone hasn't joined.
- At most one message every 3 seconds (CareBot sends far fewer).
