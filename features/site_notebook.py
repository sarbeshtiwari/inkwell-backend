"""The "Notebook" look for website-builder sites: the design of sarbeshtiwari.vercel.app (warm paper, tomato
accent, serif headlines with italic phrases, handwritten notes, taped and slightly tilted cards, sticky notes,
mono section numbers, small doodles, warm dark mode). Pure CSS on top of the site's normal markup, so every
section type and variant works; the user's colours and fonts still apply through the CSS variables."""

PRESET = {"label": "Notebook", "bg": "#fffefd", "surface": "#f6f2ea", "text": "#1a1715", "muted": "#4a433d",
          "accent": "#f25c3b", "accent2": "#3e7b9e", "font_head": "IBM Plex Serif", "font_body": "Inter",
          "background": "plain", "card": "raised", "button": "pill", "look": "notebook"}
EXTRA_FONTS = ("Caveat", "JetBrains Mono")


def _svg(body, w=48, h=48):
    from urllib.parse import quote
    return "url(\"data:image/svg+xml," + quote(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{body}</svg>') + "\")"


SPIRAL = _svg('<path d="M24 24m-2 0a2 2 0 1 1 4 0a6 6 0 1 1-12 0a10 10 0 1 1 20 0a14 14 0 1 1-28 0" fill="none" stroke="#1a1715" stroke-width="2.6" stroke-linecap="round"/>')
ASTERISK = _svg('<g stroke="#1a1715" stroke-width="3.4" stroke-linecap="round"><path d="M24 6v36"/><path d="M8.4 15l31.2 18"/><path d="M8.4 33l31.2-18"/></g>')
STAR = _svg('<path d="M24 3l5.6 14.4L45 18.5 33 28.3l4 15.2L24 35l-13 8.5 4-15.2L3 18.5l15.4-1.1z" fill="#1a1715"/>')
SPIRAL_D, ASTERISK_D, STAR_D = (s.replace("%231a1715", "%23f0ebe0") for s in (SPIRAL, ASTERISK, STAR))

TAPED = (".proj", ".tl-item", ".svc", ".txt.card", ".cform")  # cards that get a strip of tape
TAPES = ["rgb(244 180 58 / .6)", "rgb(123 166 122 / .55)", "rgb(232 155 174 / .6)", "rgb(62 123 158 / .4)"]
NOTES = ["#fbe6b4", "#dcebd6", "#f8dde3", "#d6e6ef"]
NOTES_DARK = ["#4a3b1c", "#2c3b2b", "#46292f", "#22343f"]


def css(t):
    """Extra CSS for the notebook look; dark mode only when the colours are still the notebook's own."""
    def sel(suffix=""):
        return ",".join(f".nb {x}{suffix}" for x in TAPED)
    tapes = "".join(sel(f":nth-child(4n+{i + 1})::before") + f"{{background:{c}}}" for i, c in enumerate(TAPES))
    notes = "".join(f".sk-group:nth-child(4n+{i + 1}),.quote:nth-child(4n+{i + 1}){{background:{c}}}" for i, c in enumerate(NOTES))
    notes_d = "".join(f".sk-group:nth-child(4n+{i + 1}),.quote:nth-child(4n+{i + 1}){{background:{c}}}" for i, c in enumerate(NOTES_DARK))
    own = all(t.get(k) == PRESET[k] for k in ("bg", "surface", "text", "accent"))
    dark = f"""@media (prefers-color-scheme: dark){{
:root{{--bg:#14110e;--surface:#1e1a16;--text:#f0ebe0;--muted:#b8b0a2;--line:#2c2620;--on-accent:#14110e;--doodle-spiral:{SPIRAL_D};--doodle-ast:{ASTERISK_D};--doodle-star:{STAR_D}}}
{notes_d}.sk-group *,.quote *{{color:#f0ebe0}}}}""" if own else ""
    return f"""
:root{{--hand:'Caveat','Bradley Hand','Segoe Print',cursive;--mono:'JetBrains Mono',ui-monospace,Menlo,monospace;--paper-edge:color-mix(in srgb,var(--text) 10%,var(--bg));
--doodle-spiral:{SPIRAL};--doodle-ast:{ASTERISK};--doodle-star:{STAR}}}
body{{counter-reset:sec nav;background-image:none}}
h1,h2,h3{{font-weight:400;letter-spacing:-.01em}}
em{{font-style:italic;color:var(--accent);font-weight:600}}
/* nav: handwritten name, numbered lowercase links */
.brand{{font:italic 400 19px var(--fh)}}.nav-links a{{text-transform:lowercase;font:500 15px var(--fb);background:none!important;border-radius:0}}
.nav-links a::before{{counter-increment:nav;content:"/0" counter(nav) " ";font:400 13px var(--mono);color:var(--accent2)}}
.nav-links a.on{{color:var(--accent)}}.nav-links a.on::after{{content:"";display:block;height:2px;margin-top:2px;border-radius:2px;background:var(--accent)}}
/* hero: centred serif statement, mono eyebrow, sticker badge, doodles */
#top .wrap{{position:relative}}
#top .wrap::before,#top .wrap::after{{content:"";position:absolute;width:44px;height:44px;background-size:contain;background-repeat:no-repeat;pointer-events:none;opacity:.85}}
#top .wrap::before{{left:-1%;bottom:6%;background-image:var(--doodle-spiral);animation:nb-spin 18s linear infinite}}
#top .wrap::after{{right:5%;top:12%;background-image:var(--doodle-ast);animation:nb-bob 4s ease-in-out infinite}}
@keyframes nb-spin{{to{{transform:rotate(360deg)}}}}@keyframes nb-bob{{50%{{transform:translateY(-6px) rotate(12deg)}}}}
.hero h1 .h1-main{{background:none;-webkit-text-fill-color:currentColor;color:var(--text);font:400 clamp(38px,6vw,64px)/1.12 var(--fh);letter-spacing:-.02em}}
.hero h1 .h1-main em{{-webkit-text-fill-color:var(--accent)}}
.eyebrow{{font:500 12px/1.6 var(--mono);text-transform:uppercase;letter-spacing:.18em;color:var(--muted);-webkit-text-fill-color:var(--muted);margin-bottom:18px}}
.lead{{font-size:17px}}
.badge{{font:700 19px var(--hand);background:#f4b43a;color:#1a1715;border:0;border-radius:3px;padding:2px 14px;transform:rotate(-3deg);box-shadow:0 2px 6px rgb(40 30 20 / .15)}}.badge i{{display:none}}
.hero-img img{{border-radius:4px;padding:10px 10px 34px;background:#fff;box-shadow:0 10px 30px rgb(40 30 20 / .18);transform:rotate(2deg)}}.hero-img::before{{display:none}}
html:lang(en) .hero-img::after{{content:"that's me";position:absolute;bottom:6px;left:0;right:0;text-align:center;font:400 20px var(--hand);color:#4a433d;transform:rotate(2deg);z-index:2}}
/* buttons */
.btn{{border-radius:999px!important;font-weight:600}}.btn-primary{{background:var(--accent);color:var(--on-accent);box-shadow:0 6px 16px -6px var(--accent)}}
.btn-ghost{{border:1.5px solid var(--paper-edge);background:var(--bg)}}
/* section headings: mono number, centred serif title, hand-drawn underline */
.sh{{counter-increment:sec;text-align:center;font:400 clamp(30px,4vw,42px)/1.15 var(--fh)}}
.sh::before{{content:"/0" counter(sec);display:block;font:500 11.5px var(--mono);letter-spacing:.2em;color:var(--accent2);margin-bottom:10px}}
.sh::after{{content:"";display:block;width:110px;height:9px;margin:10px auto 0;background:{_svg('<path d="M3 6 C 30 1, 60 9, 107 4" fill="none" stroke="#f4b43a" stroke-width="5" stroke-linecap="round"/>', 110, 10)} no-repeat center/contain}}
.contact-split .sh{{text-align:left}}.contact-split .sh::after{{margin-left:0}}
.intro{{text-align:center;margin-left:auto;margin-right:auto;font-family:var(--hand);font-size:22px;line-height:1.3}}
/* cards: paper with tape, a little tilt, straighten on hover */
.card{{background:var(--surface);border:1px solid var(--paper-edge);border-radius:6px;box-shadow:0 4px 10px rgb(40 30 20 / .08),0 2px 4px rgb(40 30 20 / .05);position:relative}}
{sel()}{{transition:transform .35s cubic-bezier(.34,1.56,.64,1),box-shadow .35s}}
{sel("::before")}{{content:"";position:absolute;top:-9px;left:calc(50% - 46px);width:92px;height:20px;transform:rotate(-3deg);z-index:2;pointer-events:none}}
{sel(":nth-child(odd)")}{{transform:rotate(-.6deg)}}{sel(":nth-child(even)")}{{transform:rotate(.5deg)}}
{sel(":nth-child(even)::before")}{{transform:rotate(3deg);left:auto;right:18%}}
{sel(":hover")}{{transform:rotate(0) translateY(-4px);box-shadow:0 24px 56px rgb(40 30 20 / .2),0 8px 18px rgb(40 30 20 / .12)}}
{tapes}
/* projects */
.proj{{overflow:visible}}.p-img{{margin:12px 12px 0;border-radius:4px}}.p-img.noimg{{background:var(--accent2)}}
.p-b h3{{font:700 22px var(--fh)}}.p-b p{{font-size:14.5px}}
.tags span{{font:500 10.5px var(--mono);text-transform:uppercase;letter-spacing:.08em;background:var(--bg);color:var(--accent);border:1px solid var(--accent);padding:3px 10px}}
.plink{{color:var(--accent);font-family:var(--hand);font-size:20px;font-weight:600}}.plink.live::after{{content:""}}
/* skills and quotes as sticky notes */
.sk{{grid-template-columns:repeat(auto-fit,minmax(240px,300px));justify-content:center;gap:26px}}
.about:not(.about-img-on) .prose{{max-width:720px;margin:0 auto}}
.sk-group,.quote{{border:0;border-radius:2px;box-shadow:0 6px 14px rgb(40 30 20 / .12)}}
.sk-group h3,.quote figcaption b{{font:700 26px var(--hand)}}
.sk-group:nth-child(odd),.quote:nth-child(odd){{transform:rotate(-1.4deg)}}.sk-group:nth-child(even),.quote:nth-child(even){{transform:rotate(1.2deg)}}
.chip{{background:none;border:0;padding:0 0 0 2px;color:var(--muted);font-size:14px}}.sk-chips{{gap:4px 14px}}.chip::before{{content:"· ";color:var(--accent)}}
{notes}
.bar-track i{{background:var(--accent)}}
/* timeline */
.tl-timeline::before{{background:none;border-left:2px dashed var(--paper-edge);width:0}}
.tl-timeline .tl-item::before{{background:var(--accent);box-shadow:0 0 0 4px var(--bg)}}
.tl-when{{font:600 21px var(--hand);color:var(--accent)}}.tl-item h3{{font:600 20px var(--fh)}}
/* numbers, services, cta, contact */
.stat b{{background:none;-webkit-text-fill-color:var(--accent);color:var(--accent);font:400 clamp(34px,5vw,48px)/1 var(--fh)}}.stat span{{font:500 20px var(--hand)}}
.svc-ic{{background:none;border:1.5px dashed var(--paper-edge);border-radius:50%}}.svc h3{{font:600 20px var(--fh)}}
.cta-banner{{background:var(--accent2);background-image:radial-gradient(rgb(255 255 255 / .14) 1px,transparent 1.3px);background-size:12px 12px;border-radius:6px;color:#fff}}
.cta h2{{font-weight:400}}.cta-banner h2 em{{color:#fff}}
.fact{{font-family:var(--hand);font-size:21px}}.cform input,.cform textarea{{background:var(--bg);border-radius:4px}}
.soc{{border-radius:50%}}
footer{{border-top:1px dashed var(--paper-edge)}}footer .foot>span::after{{content:"  ·  made by hand";font-family:var(--hand);font-size:18px;color:var(--accent)}}
@media(prefers-reduced-motion:reduce){{#top .wrap::before,#top .wrap::after{{animation:none}}{sel()},.sk-group,.quote{{transform:none!important}}}}
{dark}"""
