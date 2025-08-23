\# MessiahBot



Multi-tenant Discord bot with:

\- Server builder (roles, categories, channels) from a dashboard form

\- Per-guild config stored in Postgres

\- Flask dashboard to submit/update layouts

\- (Planned) Twitch schedule sync + premium features



\## Structure


messiahbot/

├─ bot/

│ ├─ messiahbot\_dc.py

│ ├─ dashboard\_messiah.py

│ ├─ commands\_messiah\_dc/

│ │ └─ server\_builder.py

│ └─ twitch\_api\_messiah.py

├─ sql/01\_messiahbot\_builder.sql

├─ web/form.html

├─ latest\_config.json

├─ requirements.txt

├─ render.yaml

└─ README.md

---

\### Local Dev instructions

```markdown

\## Local Dev


1\. Install deps:

&nbsp;  pip install -r requirements.txt


2\. Run Flask dashboard:

&nbsp;  python -m bot.dashboard\_messiah


3\. Run Discord bot:

&nbsp;  python -m bot.messiahbot\_dc


4\. Visit http://localhost:5050/form to submit a layout.


\## Deploy on Render

\- This repo includes render.yaml to set up two services:

&nbsp; - Web (dashboard) → starts dashboard\_messiah.py

&nbsp; - Worker (bot) → runs messiahbot\_dc.py


Set these environment variables in Render:

\- DISCORD\_APP\_CLIENT\_ID

\- DISCORD\_BOT\_TOKEN

\- DATABASE\_URL

\- TWITCH\_CLIENT\_ID

\- TWITCH\_CLIENT\_SECRET

\- TWITCH\_USERNAME

\- PORT (defaults to 10000 for Flask)


\## License
MIT

### Local vs Production deps
- Local dev: `pip install -r requirements.local.txt` (no Postgres client needed)
- Production/Render: uses `requirements.txt` (includes psycopg2-binary)




