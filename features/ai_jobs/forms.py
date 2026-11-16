"""Application forms: reading labels, filling contact fields, dropdowns and saved answers, learning typed answers."""

import re

from features.ai_jobs.answers import answer_for, save_answer


# (label pattern, value key). Whole words only and checked against short labels, so a question like
# "employed by us in any capacity?" can never match "city".
FIELD_MAP = [
    (r"^(legal |preferred )?first name$|^given name$", "first"), (r"^(legal )?last name$|^surname$|^family name$", "last"),
    (r"^(full |your )?name$", "name"), (r"^e-?mail( address)?$", "email"),
    (r"^(mobile |cell )?phone( number)?$|^mobile( number)?$|^contact number$", "phone"),
    (r"^((a )?link to |url (of|to) )?(your )?linkedin( profile)?( url| link| page)?$", "linkedin"),
    (r"^((a )?link to |url (of|to) )?(your )?github( profile)?( url| link| page)?$", "github"),
    (r"^((a )?link to |url (of|to) )?(your )?(portfolio|personal website|website|personal site)( url| link)?$", "portfolio"),
    (r"^current (company|employer)( name)?$", "current_company"),
    (r"^(current )?(location|city)( \(city\))?$|^where are you (based|located)\??$", "location"),
    (r"^notice period( \(.*\))?$", "notice")]


LABEL_JS = """(e => { const t = x => (x || '').replace(/[✱*]/g, '').replace(/\\s+/g, ' ').trim();
      const grp = (e.type === 'checkbox' || e.type === 'radio') && e.closest('fieldset, [role=radiogroup], [role=group], .application-question, [class*=question]');
      if (grp && grp.querySelectorAll('input[type=' + e.type + ']').length > 1) {
        const q = grp.querySelector('legend, .application-label, [class*=label]:not(label)');
        if (q && t(q.innerText)) return t(q.innerText);
      }
      if (e.labels && e.labels[0] && t(e.labels[0].innerText)) return t(e.labels[0].innerText);
      if (e.getAttribute('aria-label')) return t(e.getAttribute('aria-label'));
      const by = e.getAttribute('aria-labelledby'); if (by && document.getElementById(by)) return t(document.getElementById(by).innerText);
      const box = e.closest('li, fieldset, .application-question, [class*=question], [class*=field], [class*=Field]');
      const head = box && box.querySelector('label, legend, .application-label, .text, [class*=label], [class*=Label]');
      return t(head ? head.innerText : '') || t(e.getAttribute('placeholder')) || 'A question' })"""


CAPTCHA = "iframe[src*='captcha'], iframe[title*='captcha' i], iframe[title*='challenge' i]"


def challenge_visible(page):
    """True when a CAPTCHA puzzle is actually on screen. Invisible checks that run in the background are the site's
    own decision; a visible challenge is for a person, so the job is handed back, never solved or bypassed."""
    for f in page.locator(CAPTCHA).all():
        try:
            box = f.bounding_box()
            if f.is_visible() and box and box["height"] > 120 and box["width"] > 120:
                return True
        except Exception:
            pass
    return False


TEXT_BOXES = ("input:not([type=file]):not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit])"
              ":not([type=search]):not([role=combobox]):not([aria-autocomplete]):not([aria-haspopup]):not([readonly]), textarea")


COUNTRIES = {"91": "India", "1": "United States", "44": "United Kingdom", "971": "United Arab Emirates", "65": "Singapore",
             "61": "Australia", "49": "Germany", "33": "France", "31": "Netherlands", "353": "Ireland", "966": "Saudi Arabia",
             "974": "Qatar", "60": "Malaysia", "81": "Japan", "86": "China", "880": "Bangladesh", "92": "Pakistan",
             "94": "Sri Lanka", "977": "Nepal", "27": "South Africa", "234": "Nigeria", "254": "Kenya", "55": "Brazil"}


def pick(page, box, text):
    """Type into a searchable dropdown and choose the option that matches the user's own value; never a guess."""
    try:
        box.click(timeout=4000)
        box.fill(text, timeout=4000)
        page.wait_for_timeout(1800)  # options load as you type (city lists come from a server)
        opt = page.locator("[role=option]:visible, [class*=select__option]:visible").filter(has_text=re.compile(rf"^\s*{re.escape(text)}(?!\w)", re.I)).first
        if opt.count():
            opt.click(timeout=4000)
            page.wait_for_timeout(600)
            return True
        box.fill("", timeout=2000)
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


def fill_pickers(page, values):
    """Country, city and the phone's country code, from the user's own details."""
    done = []
    country, city = values.get("country", ""), values.get("location", "").split(",")[0].strip()
    # Read the dropdowns first, then act on each by id: choosing an option re-renders the form.
    boxes = page.evaluate("""() => [...document.querySelectorAll('input[class*=select__input], input[role=combobox]')]
        .filter(e => e.id && e.offsetParent !== null)
        .map(e => [e.id, (""" + LABEL_JS + """)(e).toLowerCase(),
                   !!e.closest('[class*=select__control], [class*=-control]')?.querySelector('[class*=single-value], [class*=singleValue]')])""")
    for bid, label, already in boxes:
        label = label.strip(" ?:")
        if already:
            continue
        box = page.locator(f"[id='{bid}']")
        if re.fullmatch(r"country( of residence)?", label) and country and pick(page, box, country):
            done.append("country")
        elif re.fullmatch(r"(current )?(location|city)( \(city\))?", label) and city and pick(page, box, city):
            done.append("city")
    lever = page.locator("#location-input:visible").first  # Lever clears the box unless a suggestion is picked
    if city and lever.count():
        try:
            lever.fill("", timeout=3000)
            lever.press_sequentially(city, delay=40, timeout=8000)
            opt = page.locator(".dropdown-location:visible").filter(has_text=re.compile(rf"^\s*{re.escape(city)}(?!\w)", re.I)).first
            opt.wait_for(state="visible", timeout=6000)
            opt.click(timeout=4000); done.append("location")
        except Exception:
            pass
    flag = page.locator(".iti__selected-country, .iti__selected-flag, .iti__flag-container button").first
    if country and values.get("dial") and flag.count():
        try:
            if f"+{values['dial']}" not in (flag.get_attribute("title") or "") + flag.inner_text():
                flag.click(timeout=4000)
                page.locator(".iti__search-input").first.fill(country, timeout=4000)
                page.wait_for_timeout(500)
                item = page.locator(f".iti__country[data-dial-code='{values['dial']}']").first
                if item.count():
                    item.click(timeout=4000); done.append("phone country code")
                else:
                    page.keyboard.press("Escape")
        except Exception:
            pass
    return done


REMAINING_JS = """(mark) => [...document.querySelectorAll('input[required], textarea[required], select[required], [aria-required=true]')]
        .filter(e => {
            if (e.type === 'file' || e.type === 'hidden') return false;
            const pickerHasValue = x => !!x.closest('[class*=select__control], [class*=-control]')?.querySelector('[class*=single-value], [class*=singleValue], [class*=multi-value]');
            if (e.matches('[class*=select__input], [role=combobox]') && pickerHasValue(e)) return false;
            if (e.getAttribute('aria-hidden') === 'true' && e.parentElement && pickerHasValue(e.parentElement.querySelector('input:not([aria-hidden])') || e)) return false;
            if (!e.matches('input, textarea, select'))  // a group, like an upload box: answered when a file is attached
                return !([...e.querySelectorAll('input[type=file]')].some(f => f.files.length) || /\\.(pdf|docx?|txt|rtf)\\b/i.test(e.innerText));
            if (e.offsetParent === null && e.getAttribute('aria-hidden') !== 'true') return false;
            return !(e.type === 'checkbox' || e.type === 'radio' ? document.querySelector(`input[name="${e.name}"]:checked`) : e.value);
        })
        .map(e => { if (mark) (e.closest('.select-shell, .field, .file-upload, fieldset, li') || e.parentElement || e).style.outline = '2px solid #e5484d';
            return (" + LABEL_JS + ")(e).slice(0, 70) + (e.getAttribute('aria-hidden') === 'true' ? ' (pick from the list)' : '') })""".replace('" + LABEL_JS + "', LABEL_JS)


SNAPSHOT_JS = """() => {
  const t = x => (x || '').replace(/[✱*]/g, '').replace(/\\s+/g, ' ').trim();
  const lab = e => (""" + LABEL_JS + """)(e);
  const out = [];
  document.querySelectorAll('textarea, input[type=text], input:not([type])').forEach(e => {
    if (e.offsetParent === null || e.matches('[class*=select__input], [role=combobox], [type=search]') || !e.value.trim()) return;
    out.push([lab(e), e.value.trim()]);
  });
  document.querySelectorAll('[class*=select__control], [class*=-control]').forEach(c => {
    const v = c.querySelector('[class*=single-value], [class*=singleValue]'); const i = c.querySelector('input');
    if (v && i && !c.closest('.iti')) out.push([lab(i), t(v.innerText)]);
  });
  document.querySelectorAll('select').forEach(e => { if (e.offsetParent !== null && e.value && e.selectedIndex > 0) out.push([lab(e), t(e.options[e.selectedIndex].text)]); });
  const seen = new Set();
  document.querySelectorAll('input[type=radio]:checked, input[type=checkbox]:checked').forEach(e => {
    const box = e.closest('fieldset, [role=radiogroup], [role=group], .application-question, [class*=question]') || e.closest('li, [class*=field]');
    const single = e.type === 'checkbox' && box && box.querySelectorAll('input[type=checkbox]').length === 1;
    const q = single ? t((box.querySelector('legend, label, [class*=label]') || {}).innerText) : lab(e);
    const a = single ? 'Yes' : t(e.labels && e.labels[0] ? e.labels[0].innerText : e.value);
    const k = q + '|' + e.name; if (!q) return;
    if (seen.has(k)) { const row = out.find(r => r[2] === k); if (row) row[1] += ', ' + a; return; }  // tick-all-that-apply: one answer
    seen.add(k); out.push([q, a, k]);
  });
  document.querySelectorAll('button[aria-pressed=true], [role=radio][aria-checked=true]').forEach(e => {
    const box = e.closest('fieldset, [role=radiogroup], [class*=question], [class*=field]');
    const q = box ? t((box.querySelector('legend, label, [class*=label]') || {}).innerText) : '';
    if (q) out.push([q, t(e.innerText)]);
  });
  return out.filter(([q, a]) => q && a && q.length < 300).map(([q, a]) => [q, a]);
}"""


def learn_from_page(ws, page, job):
    """Remember the answers the user typed themselves in this form, for their review (never used before approval)."""
    try:
        pairs = page.evaluate(SNAPSHOT_JS)
    except Exception:
        return 0
    n = 0
    for q, a in pairs:
        ql = q.lower().strip(" ?:")
        if any(re.search(pat, ql) for pat, _ in FIELD_MAP) or re.search(r"resume|cover letter|password|captcha", ql):
            continue  # contact details and files come from the profile already
        specific = bool(job.get("company")) and job["company"].lower() in q.lower()
        save_answer(ws, q, a, job["company"] if specific else "", status="review", source="learned",
                    learned_from=f"{job['company']} · {job['title']}")
        n += 1
    return n


def fill_from_bank(page, answers, company, live):
    """Answer questions with the user's approved answers: text, dropdowns, radio groups and Yes/No buttons."""
    done = []
    if not answers:
        return done
    for el in page.locator(TEXT_BOXES).all():
        try:
            if not el.is_visible() or el.input_value():
                continue
            label = el.evaluate("e => (" + LABEL_JS + ")(e)") or ""
            ans = answer_for(answers, label, company)
            if ans:
                el.scroll_into_view_if_needed(timeout=2000)
                el.fill(ans, timeout=5000); done.append(label[:60]); live(page, f"Answered “{label[:50]}”")
        except Exception:
            continue
    boxes = page.evaluate("""() => [...document.querySelectorAll('input[class*=select__input], input[role=combobox]')]
        .filter(e => e.id && e.offsetParent !== null && !e.closest('.iti')
                && !e.closest('[class*=select__control], [class*=-control]')?.querySelector('[class*=single-value], [class*=singleValue]'))
        .map(e => [e.id, (""" + LABEL_JS + """)(e)])""")
    for bid, label in boxes:
        ans = answer_for(answers, label, company)
        if ans and pick(page, page.locator(f"[id='{bid}']"), ans):
            done.append(label[:60]); live(page, f"Answered “{label[:50]}”")
    for sel in page.locator("select").all():
        try:
            if not sel.is_visible() or sel.evaluate("e => e.selectedIndex > 0"):
                continue
            label = sel.evaluate("e => (" + LABEL_JS + ")(e)") or ""
            ans = answer_for(answers, label, company)
            if ans:
                sel.select_option(label=ans, timeout=3000); done.append(label[:60]); live(page, f"Answered “{label[:50]}”")
        except Exception:
            continue
    groups = page.evaluate("""() => {
        const t = x => (x || '').replace(/[✱*]/g, '').replace(/\\s+/g, ' ').trim(); const out = []; let n = 0;
        const lever = [...document.querySelectorAll('.application-question')].filter(g => g.querySelector('input[type=radio], input[type=checkbox]'));
        [...document.querySelectorAll('fieldset, [role=radiogroup]'), ...lever].forEach(g => {
          if (g.offsetParent === null || g.querySelector('input:checked, [aria-checked=true], [aria-pressed=true]')) return;
          const q = t((g.querySelector('legend, .application-label') || g.querySelector('label, [class*=label]') || {}).innerText);
          if (!q) return; g.setAttribute('data-ro-group', String(++n)); out.push([String(n), q]);
        });
        return out; }""")
    for gid, label in groups:
        ans = answer_for(answers, label, company)
        if not ans:
            continue
        g = page.locator(f"[data-ro-group='{gid}']")
        if g.locator("input[type=checkbox]").count() > 1:  # tick all that apply: the answer lists the options
            ticked = 0
            for part in filter(None, (x.strip() for x in ans.split(","))):
                box = g.locator("label").filter(has_text=re.compile(rf"^\s*{re.escape(part)}\s*$", re.I)).first
                try:
                    if box.count():
                        box.scroll_into_view_if_needed(timeout=2000); box.click(timeout=3000); ticked += 1
                except Exception:
                    continue
            if ticked:
                done.append(label[:60]); live(page, f"Answered “{label[:50]}”")
            continue
        opt =g.locator("label, button, [role=radio]").filter(has_text=re.compile(rf"^\s*{re.escape(ans)}\s*$", re.I)).first
        try:
            if opt.count():
                opt.scroll_into_view_if_needed(timeout=2000); opt.click(timeout=3000)
                done.append(label[:60]); live(page, f"Answered “{label[:50]}”")
            elif re.fullmatch(r"yes|i agree|agree|true", ans.strip(), re.I) and g.locator("input[type=checkbox]").count() == 1:
                g.locator("input[type=checkbox]").first.check(timeout=3000); done.append(label[:60])
        except Exception:
            continue
    return done


def questions_left(page, mark=False):
    """Required questions still unanswered, one entry per question (a dropdown's hidden copy isn't counted twice)."""
    out = {}
    missing = page.evaluate(REMAINING_JS, mark)
    for m in missing:
        hidden = m.endswith(" (pick from the list)")
        base = re.sub(r"\s*select\.\.\.$", "", m.removesuffix(" (pick from the list)"), flags=re.I).strip()
        if base == "A question" and len(missing) > 1:
            continue
        key = base.lower()
        if hidden and key in out:
            continue
        out.setdefault(key, base + (" (choose from the list)" if hidden else ""))
    return sorted(out.values())


def file_input_label(f):
    try:
        return (" ".join(filter(None, [f.get_attribute("name"), f.get_attribute("id"), f.get_attribute("aria-label")])) + " "
                + (f.evaluate("e => (" + LABEL_JS + ")(e)") or "")).lower()
    except Exception:
        return ""


def fill_form(page, values, resume_path, live=lambda page, step="": None, answers=None, company="", cover=None):
    """Fill what we can from the user's own details; return the labels of required questions left empty.

    cover = {"path": file to attach, "text": the letter's text} when the user has a cover letter to send."""
    filled = []
    files = page.locator("input[type=file]")
    for i in range(files.count()):
        f = files.nth(i)
        attrs = file_input_label(f)
        if "cover" in attrs:
            continue
        if "resume" in attrs or "cv" in attrs or i == 0:
            f.set_input_files(resume_path); filled.append("Resume"); live(page, "Attached your resume"); break
    if cover and cover.get("path"):
        for i in range(files.count()):
            f = files.nth(i)
            try:
                if "cover" in file_input_label(f) and not f.evaluate("e => e.files.length"):
                    f.set_input_files(cover["path"]); filled.append("Cover letter"); live(page, "Attached your cover letter"); break
            except Exception:
                continue
    if filled:  # some forms read the resume and refill (and reset) the fields; let that finish first
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
    for el in page.locator(TEXT_BOXES).all():
        try:
            if not el.is_visible() or el.input_value():
                continue
            if el.evaluate("e => e.type !== 'tel' && !!e.closest('[class*=select__], [class*=-select], [class*=dropdown], [role=listbox]')"):
                continue  # a dropdown that only looks like a text box: answers there are the user's call
            label = (el.evaluate("e => (" + LABEL_JS + ")(e)") or "").lower().strip(" ?:")
        except Exception:
            continue
        if cover and cover.get("text") and re.fullmatch(r"(your |a )?cover letter( \(optional\))?|cover letter / message|message to (the )?hiring (team|manager)", label):
            try:
                el.scroll_into_view_if_needed(timeout=2000)
                el.fill(cover["text"], timeout=8000); filled.append("Cover letter (pasted)"); live(page, "Pasted your cover letter")
            except Exception:
                pass
            continue
        if len(label) > 40:
            continue  # a real question, not a contact field
        for pat, key in FIELD_MAP:
            if re.search(pat, label) and values.get(key):
                try:
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.fill(values[key], timeout=5000); filled.append(label)
                    live(page, f"Filled {label}")
                except Exception:
                    pass
                break
    picked = fill_pickers(page, values)
    filled += picked
    if picked:
        live(page, "Chose " + ", ".join(picked))
    from_bank = fill_from_bank(page, answers, company, live)
    filled += [f"answer: {x}" for x in from_bank]
    missing = questions_left(page)
    return filled, missing
