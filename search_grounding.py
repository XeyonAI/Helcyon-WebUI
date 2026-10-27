"""Grounding rules shared by every web-search path (local ChatML, native
messages API, OpenAI, Anthropic).

Pure functions, no I/O and no model calls. Three jobs:

* ``RESULTS_RULE`` is the one instruction that follows a results block. The
  four paths used to carry different wording; three of them told the model to
  treat the results as "ground truth", to answer "as if you just know this"
  and to omit sources, so search facts entered the chat history as the
  character's own unattributed knowledge.
* ``filter_relevant`` drops result items that share no topical word with the
  query and reports when nothing relevant is left, so an off-topic result set
  is reported as such instead of being handed to the model to "discuss".
* ``strip_search_ui`` removes HWUI's own search display text (the
  "Searching..." marker, the source-link anchors and the search notices) from
  an earlier assistant turn before it is sent back to a model. The chat file
  and the page keep them; only the model-facing copy is cleaned.
"""
import math
import re

RESULTS_RULE = (
    "These are real results returned by HWUI for this request. Base factual claims only on "
    "these results and say which source a claim comes from when it matters. If a requested "
    "detail is absent, say so plainly rather than filling it in. Do not present the results "
    "as something you already knew. HWUI adds the source links below your reply, so you do "
    "not need to write URLs. Answer naturally without mentioning the trigger, tool contract, "
    "or result-block structure."
)

IRRELEVANT_NOTICE = (
    "🔍 The search didn’t return anything relevant to this request, so I can’t verify it "
    "from live results."
)

CONTEXT_FULL_NOTICE = (
    "🔍 The search results don’t fit in the space left in this conversation, so I can’t use "
    "them. A new chat or a shorter request will leave room."
)

# Words that never decide whether a result is about the query: grammar, filler,
# and the time words that every news page contains.
_STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing done down during each few for
from further get got had has have having he her here hers him his how i if in into is it its
just me more most my no nor not now of off on once only or other our out over own same she
should so some such than that the their them then there these they this those through to too
under until up very was we were what when where which while who whom why will with would you
your yours yes yeah ok okay please thanks tell know find check look search online web internet
google lookup results result news latest newest recent recently current currently today
tonight yesterday tomorrow week weeks month months year years last next night morning
evening time times new update updates happened happening anything something thing things
big top best really http https www com org net
""".split())

# Hyphens, dots and slashes separate words, so URLs and "top-stories" count.
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’]*")


def _terms(text, query=True):
    """Topical words of `text`. Result text (query=False) keeps every short word,
    so a query's "UK" still matches "bbc.co.uk"."""
    terms = set()
    for raw in _WORD_RE.findall(str(text or "")):
        word = raw.lower().replace("’", "'").strip("'")
        if word.endswith("'s"):
            word = word[:-2]
        if not word or word in _STOPWORDS:
            continue
        # Short words count only as acronyms (UK, AI) or with a digit (F1, 4K).
        if (not query or len(word) >= 3 or any(ch.isdigit() for ch in word)
                or (len(raw) >= 2 and raw.isupper())):
            terms.add(word)
    return terms


def _matches(term, text_terms):
    if term in text_terms:
        return True
    # drive/driving, release/released: a shared 5-letter stem counts.
    return len(term) >= 5 and any(len(t) >= 5 and t[:5] == term[:5] for t in text_terms)


def _matched(query_terms, *texts):
    text_terms = _terms(" ".join(str(t or "") for t in texts), query=False)
    return {term for term in query_terms if _matches(term, text_terms)}


# Words that say which instalment, never which show: every show has a "season 2"
# and an "episode 9". On 25 Oct a MyAnimeList page for another show's 2nd
# season shared only "season" with "… season two episode nine …", was kept,
# sat last before the reply instructions, and GPT-4o described that show
# (six months of dating, Christmas) as Dark Matter season 2 episode 9.
_INSTALMENT_TERMS = frozenset("""
season seasons series episode episodes ep part chapter volume finale premiere upcoming final recap
one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen
seventeen eighteen nineteen twenty
""".split())


def _instalment_term(term):
    return term in _INSTALMENT_TERMS or term.isdigit() or bool(re.fullmatch(r"s\d+e\d+|\d+(?:st|nd|rd|th)", term))


_NUMBER_WORD_LIST = ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                     "fifteen sixteen seventeen eighteen nineteen twenty").split()
_NUM_OR_WORD = r"(\d{1,3}|" + "|".join(_NUMBER_WORD_LIST) + r")"
_SEASON_NUM_RE = re.compile(r"\b(?:season|series)[\s_-]*" + _NUM_OR_WORD + r"\b|\bs0*(\d{1,2})[\s_-]*e\d", re.IGNORECASE)
_EPISODE_NUM_RE = re.compile(r"\b(?:episode|ep)[\s_.-]*" + _NUM_OR_WORD + r"\b|\bs\d{1,2}[\s_-]*e0*(\d{1,3})\b",
                             re.IGNORECASE)


def _instalment_numbers(text):
    """{"season": {...}, "episode": {...}} named in `text` ("S2E9", "season-2-episode-9", "season two")."""
    def values(pattern):
        found = set()
        for match in pattern.finditer(str(text or "")):
            raw = next(g for g in match.groups() if g)
            found.add(_NUMBER_WORD_LIST.index(raw.lower()) + 1 if raw.lower() in _NUMBER_WORD_LIST else int(raw))
        return found
    return {"season": values(_SEASON_NUM_RE), "episode": values(_EPISODE_NUM_RE)}


def _other_instalment(query_numbers, *texts):
    """True when `texts` (a result's title and URL) name a different season or
    episode than the query: "Dark Matter | Season 1 - Episode 9" for season 2
    episode 9 was kept and linked in the 25 Oct live run."""
    item = _instalment_numbers(" ".join(str(t or "") for t in texts))
    return any(query_numbers[field] and item[field] and not (query_numbers[field] & item[field])
               for field in ("season", "episode"))


def filter_relevant(query, res):
    """Return ``(filtered, relevant)`` for one provider result dict.

    Pages and listed results that share no topical word with the query are
    dropped. When the query names a title and an instalment ("Dark Matter
    season 2 episode 9"), an item must share a title word: the instalment
    words alone match every show. ``relevant`` is False when what remains
    covers too little of the query (at least one term, and a third of them for
    longer queries), or none of its title words. A query with no topical
    words, or an empty result set, is passed through unchanged. The input dict
    is not modified.
    """
    if not isinstance(res, dict):
        return res, False
    query_terms = _terms(query)
    has_content = any(res.get(key) for key in ("summary", "top_text", "pages", "results"))
    # Nothing to judge: each path's own "no results" handling applies.
    if not query_terms or not has_content:
        return res, True
    subject_terms = {term for term in query_terms if not _instalment_term(term)}
    if subject_terms == query_terms:
        subject_terms = set()   # no instalment words: any shared word counts, as before

    def on_topic(hit):
        return bool(hit & subject_terms) if subject_terms else bool(hit)

    filtered = dict(res)
    covered = set()
    # A result whose title or URL names another season or episode is about a
    # different instalment (body text is not read: recaps mention earlier ones).
    query_numbers = _instalment_numbers(query)

    pages = []
    for page in res.get("pages") or []:
        if not isinstance(page, dict):
            continue
        hit = _matched(query_terms, page.get("title"), page.get("text"), page.get("url"))
        if on_topic(hit) and not _other_instalment(query_numbers, page.get("title"), page.get("url")):
            pages.append(page)
            covered |= hit
    results = []
    for item in res.get("results") or []:
        if not isinstance(item, dict):
            continue
        hit = _matched(query_terms, item.get("title"), item.get("snippet"), item.get("url"))
        if on_topic(hit) and not _other_instalment(query_numbers, item.get("title"), item.get("url")):
            results.append(item)
            covered |= hit
    filtered["pages"] = pages
    filtered["results"] = results

    summary_hit = _matched(query_terms, res.get("summary"))
    if not on_topic(summary_hit):
        filtered["summary"] = ""
        summary_hit = set()
    covered |= summary_hit

    top_hit = _matched(query_terms, res.get("top_text"), res.get("top_url"))
    # The top page is one of the pages, so another instalment's page must not
    # come back through it: "Episode 4: The Corridor" was dropped from pages
    # for season 4 episode 3, yet its text was given and its link shown (26 Oct).
    top_title = next((p.get("title") for p in res.get("pages") or []
                      if isinstance(p, dict) and p.get("url") == res.get("top_url")), "")
    top_other = _other_instalment(query_numbers, top_title, res.get("top_url"))
    if not on_topic(top_hit) or top_other:
        top_hit = set()
    # The top URL is usually where the summary came from, so a relevant
    # summary keeps it even when the URL itself names nothing.
    if not top_hit and subject_terms:
        # An off-topic top page is neither shown to the model nor linked.
        filtered["top_text"] = pages[0].get("text", "") if pages else ""
        filtered["top_url"] = (pages[0].get("url", "") if pages
                               else res.get("top_url", "") if summary_hit and not top_other else "")
    elif top_other or (not top_hit and not pages and not summary_hit):
        filtered["top_text"] = ""
        filtered["top_url"] = ""
    covered |= top_hit
    if pages and not filtered.get("top_url"):
        filtered["top_url"] = pages[0].get("url", "")

    needed = max(1, math.ceil(len(query_terms) / 3))
    return filtered, len(covered) >= needed and (not subject_terms or bool(covered & subject_terms))


def source_links(res, reply, limit=3):
    """``(url, title)`` pairs to show under a searched reply.

    Fetched pages first, then the top URL, then listed results; every one was
    in the evidence given to the model, and none is already in the reply.
    Listed results are the last resort: when every page was dropped as off
    topic, the reply was built from the results but showed no source at all
    (26 Oct live run).
    """
    reply = str(reply or "")
    links = []

    def add(url, title):
        if url and url not in reply and all(url != u for u, _t in links):
            links.append((url, (title or url).strip() or url))

    for page in (res.get("pages") or [])[:limit]:
        if isinstance(page, dict):
            add(page.get("url") or "", page.get("title"))
    if not links and not res.get("pages"):
        # Its title comes from the listed result for the same URL, when there is
        # one: a bare URL was shown as the link text (26 Oct, Severance).
        top_url = res.get("top_url") or ""
        add(top_url, next((item.get("title") for item in res.get("results") or []
                           if isinstance(item, dict) and item.get("url") == top_url), ""))
    if not links and not res.get("pages") and not res.get("top_url"):
        for item in (res.get("results") or [])[:limit]:
            if isinstance(item, dict):
                add(item.get("url") or "", item.get("title"))
    return links[:limit]


_SEARCHING_MARKER_RE = re.compile(r"\n*🔍 \*Searching\.\.\.\*\n*")
_SOURCE_ANCHOR_RE = re.compile(r"\s*<a href=\"[^\"]*\" target=\"_blank\"[^>]*>🔗[^<]*</a>")
_NOTICE_RES = (
    re.compile(r"\n*\*🔍 No web search was run[^*]*\*"),
    re.compile(r"\n*⚠️ \*No Brave Search key configured[^*]*\*\n*"),
    re.compile(r"⚠️ Web search failed\. No verified results are available; please try again later\."),
    re.compile(r"🔍 The search returned no usable results or source links, so I can’t verify the "
               r"requested information\."),
    re.compile(re.escape(IRRELEVANT_NOTICE)),
    re.compile(re.escape(CONTEXT_FULL_NOTICE)),
    re.compile(r"\n⚠️ Search (?:error|failed): [^\n]*"),
)


def strip_search_ui(text):
    """Remove HWUI's search display text from one assistant turn's text."""
    if not isinstance(text, str) or ("🔍" not in text and "🔗" not in text and "⚠️" not in text):
        return text
    cleaned = _SOURCE_ANCHOR_RE.sub("", text)
    cleaned = _SEARCHING_MARKER_RE.sub("\n\n", cleaned)
    for pattern in _NOTICE_RES:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


def strip_search_ui_from_history(messages):
    """Copy of a history list with search display text removed from assistant turns."""
    cleaned = []
    for message in messages or []:
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str):
                stripped = strip_search_ui(content)
                if stripped != content:
                    message = {**message, "content": stripped}
            elif isinstance(content, list):
                parts = [
                    {**part, "text": strip_search_ui(part.get("text", ""))}
                    if isinstance(part, dict) and part.get("type") == "text" else part
                    for part in content
                ]
                message = {**message, "content": parts}
        cleaned.append(message)
    return cleaned


# ── Automatic search for current topics (per character) ──────────────────────
# Explicit requests ("search online for …") always search. Without one, a
# character with auto_search_current_topics on (the default for search-enabled
# cards) may search a message that asks about something current. Deciding that
# is two-stage so ordinary conversation costs nothing: recency_candidate() is a
# regex pre-filter, and only a message it passes is shown to the model-judged
# gate (app._search_intent_gate, one short call that fails closed).
CARD_AUTO_SEARCH = "auto_search_current_topics"


def card_auto_searches(card):
    """True when automatic current-topic search is allowed for this card.

    Requires web search on; absent means on, so existing search-enabled cards
    gain it until the user switches it off for that character.
    """
    card = card or {}
    return bool(card.get("use_web_search")) and card.get(CARD_AUTO_SEARCH) is not False


# Words that say the user is asking about something that happened or changed in
# the world. Generic time words alone ("today", "right now") are weak: "how was
# your day today?" is not a lookup. They count only beside a proper noun.
_STRONG_CURRENCY_RE = re.compile(
    r"\b(?:news|headlines?|latest|newest|announc(?:e|ed|ement)|releas(?:e|ed|ing)|"
    r"launch(?:ed|ing)?|came\s+out|coming\s+out|out\s+yet|release\s+date|breaking|"
    r"just\s+(?:dropped|came\s+out)|rumou?rs?|leak(?:s|ed)?|patch(?:\s+notes)?|update(?:d|s)?|"
    r"trailer|scores?|who\s+won|winners?|results?|standings|price\s+of|cost\s+of|"
    r"stock\s+price|election|happened\s+(?:with|to|at|in)|"
    # Conditions that are only ever current.
    r"weather|forecast|exchange\s+rate)\b",
    re.IGNORECASE,
)
_WEAK_TIME_RE = re.compile(
    r"\b(?:today|tonight|yesterday|this\s+(?:morning|week|weekend|month|year)|"
    r"last\s+(?:night|week|weekend|month)|right\s+now|currently|at\s+the\s+moment|recent(?:ly)?)\b",
    re.IGNORECASE,
)
# The message asks the assistant about the world rather than sharing.
_ASKS_RE = re.compile(
    r"\?|\b(?:did\s+you\s+(?:hear|see|know|catch)|have\s+you\s+(?:heard|seen)|"
    r"any\s+(?:news|word|updates?|idea)|what(?:'s|\s+is|\s+are)\s+(?:the\s+)?(?:latest|new|happening)|"
    r"what\s+happened|is\s+(?:it|there|the\b[^.?!]{0,40})\s+out|tell\s+me\s+(?:about|what))\b"
    # A question to the assistant with or without its "?": voice input usually
    # has neither the mark nor sentence breaks ("… Do you want to look that up
    # and we'll chat about it.", "i just finished silo season two what did you
    # think of the ending", 25 Oct). The rest of recency_candidate still needs
    # a currency word, a time word or an instalment, and the gate decides.
    r"|\b(?:do|did|can|could|would|will|have|are)\s+you\b"
    r"|(?:^|[.!?]\s+)(?:what'?s|whats|what|who|when|where|which|how|is|are|has|have|was|were)\b"
    # Wondering aloud is asking.
    r"|\bi\s+wonder\b|\bi'?m\s+curious\b|\bi'?d\s+(?:love|like)\s+to\s+know\b",
    re.IGNORECASE,
)
# A softer request to find something out ("can you check what the reviews say
# about …", "see if there's any news on …", "could you find out …"). Not an
# explicit search request: with a named subject it makes the message a
# candidate for the gate; with nothing named ("can you find out where she is?")
# it stays conversation.
_SOFT_LOOKUP_RE = re.compile(
    r"(?:\b(?:can|could|would|will)\s+you\s+|\byou\s+(?:could|can)\s+|(?:^|[.!?,]\s*)(?:please\s+)?)"
    r"(?:check|find\s+out|see\s+(?:if|whether|what)|look\s+into|dig\s+up|read\s+up\s+on)\b",
    re.IGNORECASE,
)
# Instalment numbers as typed or as speech-to-text writes them ("season two").
_NUMBER_WORDS = ("one two three four five six seven eight nine ten eleven twelve thirteen "
                 "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()
_NUM = r"(?:\d+|" + "|".join(_NUMBER_WORDS) + r")"


def _number(word):
    word = word.lower()
    return _NUMBER_WORDS.index(word) + 1 if word in _NUMBER_WORDS else int(word)


# An instalment of a show, series, game or book ("the season 3 finale of Silo",
# "Severance S02E10", "the new season of The Bear"). Beside a proper noun it
# may well be newer than the model's training, and with no currency word or
# time word ("I've just finished the season 3 finale of Silo … what did you make
# of it?") the gate was never asked and the model described an ending it had
# never been given (24 Oct). "Next" and "upcoming" count too: "what's going on
# with the next season of Silo?" never reached the gate (25 Oct).
_INSTALMENT_RE = re.compile(
    r"\b(?:finale|premiere|(?:season|series|episode)\s+" + _NUM + r"|s\d{1,2}\s*e\d{1,2}|"
    r"(?:new|next|upcoming|latest|final)\s+(?:season|series|episode|chapter|book|album|film|movie|game|expansion|dlc))\b",
    re.IGNORECASE,
)
# A capitalised word that is not the first word of its sentence and not "I".
_PROPER_NOUN_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b(?!I\b)[A-Z][A-Za-z0-9]+")
# Emoji between sentences would hide the sentence start from _PROPER_NOUN_RE
# ("…with you! 😁 Have you…" made "Have" a proper noun).
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]+")
# Talk about the user's own life or about this conversation.
_PERSONAL_RE = re.compile(
    r"^\s*(?:i\s+(?:feel|felt|am|was|think|thought|just|had|have)|my\s+|we\s+(?:had|were|went))\b"
    r"|\b(?:your\s+day|you\s+(?:feel|doing)|about\s+(?:me|you|us)|our\s+(?:chat|conversation)|"
    r"(?:with|between)\s+(?:us|you\s+and\s+me|me\s+and\s+you)|on\s+your\s+end)\b",
    re.IGNORECASE,
)


def recency_candidate(text):
    """Cheap pre-filter: might this message want live information?

    No model call. True only for a question-shaped message carrying a strong
    currency word, or a time word or a named instalment (season, episode,
    finale…) beside a proper noun, and not opening as talk about the user's own
    life. A True answer is a reason to ask the gate, never a reason to search
    on its own.
    """
    text = _screen_text(text)
    if not text or not _ASKS_RE.search(text):
        return False
    return _currency_signal(text)


def current_topic_signal(text):
    """recency_candidate's subject test without its question test.

    For a search the MODEL proposes on an automatic-search turn: voice input
    often has no question shape ("Babe, what's going on with the Dark Matter
    season 2 finale these days"), but a named instalment, a strong currency
    word, or a time word beside a proper noun still marks a current topic.
    Talk about the user's own life ("I'm so tired today") does not.
    """
    text = _screen_text(text)
    return bool(text) and _currency_signal(text)


def _screen_text(text):
    text = re.sub(r"\s+", " ", _EMOJI_RE.sub(" ", str(text or ""))).strip()
    # A long message (a voice note, a story) is judged on its ending, where the
    # question usually is: it used to be skipped whole past 600 characters.
    if len(text) > 600:
        text = text[-600:].split(" ", 1)[-1]
    return text


def _currency_signal(text):
    # A lower-case show title names a subject too ("i just finished silo season two").
    if _PERSONAL_RE.search(text) and not (_PROPER_NOUN_RE.search(text) or _lowercase_title(text)):
        return False
    if _STRONG_CURRENCY_RE.search(text):
        return True
    named = bool(_PROPER_NOUN_RE.search(text))
    if _SOFT_LOOKUP_RE.search(text) and named:
        return True
    if _INSTALMENT_RE.search(text) and (named or _lowercase_title(text)):
        return True
    return bool(_WEAK_TIME_RE.search(text) and named)


# The instalment words a query keeps ("season 3 finale", "S02E08").
_INSTALMENT_PART_RE = re.compile(
    r"\b(?:season|series|episode)\s+" + _NUM + r"\b|\bs\d{1,2}\s*e\d{1,2}\b|\b(?:finale|premiere)\b|"
    r"\b(?:new|next|upcoming|latest|final)\s+(?:season|series|episode|chapter|book|album|film|movie|game|expansion|dlc)\b",
    re.IGNORECASE,
)
# A title: capitalised words, optionally joined by of/the/and ("Game of Thrones",
# "The Bear"), not starting a sentence (same rule as _PROPER_NOUN_RE).
_TITLE_RE = re.compile(
    r"(?<![.!?]\s)(?<!^)\b(?!I\b)[A-Z][A-Za-z0-9]*(?:['’-][A-Za-z0-9]+)*"
    r"(?:\s+(?:(?:of|the|and)\s+)?[A-Z][A-Za-z0-9]*(?:['’-][A-Za-z0-9]+)*)*"
)


def instalment_query(text):
    """A search query for a question about a named show's season, episode or finale, or "".

    These skip the model-judged intent gate: on 24 Oct the live gate answered
    NO_SEARCH for every named episode or finale question, verbatim copies of
    its own SEARCH examples included, while getting prices, scores, weather
    and releases right, so Aurora described a Silo finale she had never seen.
    A recency candidate that names an instalment and a title is specific
    enough on its own. The query is the title nearest the instalment words
    plus those words: "Silo season 3 finale".
    """
    if not recency_candidate(text):
        return ""
    return instalment_topic(text)


# Words that sit in front of "season two" without being its title.
_NOT_A_TITLE = _STOPWORDS | frozenset("""
watched watching watch finished finishing finish seen saw started starting start binged binge caught
rewatched rewatching loved love loves liked like likes hated hate enjoyed enjoying into onto
""".split())


def _lowercase_title(text):
    """The words just before a numbered season/episode when nothing is capitalised.

    Voice input arrives lower case: "i just finished silo season two" names
    "silo" without a capital, so _TITLE_RE finds no title (25 Oct corpus).
    Up to three words directly in front of the first numbered instalment,
    stopping at any word that cannot be a title. "" when there is none.
    """
    first = next((m for m in _INSTALMENT_PART_RE.finditer(text)
                  if re.search(r"\b" + _NUM + r"\b|\d", m.group(0), re.IGNORECASE)), None)
    if not first:
        return ""
    words = []
    for raw in reversed(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text[:first.start()])[-3:]):
        word = raw.lower().strip("'’")
        if word in _NOT_A_TITLE or len(word) < 3:
            break
        words.insert(0, raw)
    return " ".join(words)


def instalment_topic(text):
    """The "<title> <instalment words>" query for a named instalment in `text`, or "".

    instalment_query without the question test: also used for the topic of a
    pronoun lookup ("… season two, episode nine of Dark Matter. Do you want to
    look that up?"), where the request itself names nothing.
    """
    text = re.sub(r"\s+", " ", _EMOJI_RE.sub(" ", str(text or ""))).strip()
    parts = list(_INSTALMENT_PART_RE.finditer(text))
    if not parts:
        return ""
    # Blank the instalment words so "Andor S02E08" yields the title "Andor".
    masked = _INSTALMENT_PART_RE.sub(lambda m: " " * len(m.group(0)), text)
    titles = list(_TITLE_RE.finditer(masked))
    if titles:
        anchor = parts[0].start()
        title = min(titles, key=lambda m: min(abs(m.start() - anchor), abs(m.end() - anchor)))
        name = re.sub(r"['’]s$", "", title.group(0).strip())
    else:
        name = _lowercase_title(text)
        if not name:
            return ""
    return f"{name} {_instalment_words(parts)}"


def instalment_words(text):
    """Just the instalment words in `text` ("season 3", "S02E09"), or ""."""
    text = re.sub(r"\s+", " ", _EMOJI_RE.sub(" ", str(text or ""))).strip()
    parts = list(_INSTALMENT_PART_RE.finditer(text))
    return _instalment_words(parts) if parts else ""


def _instalment_words(parts):
    # "the latest episode … season 2, episode 9": the numbers say which one.
    if any(re.search(r"\b" + _NUM + r"\b|\d", p.group(0), re.IGNORECASE) for p in parts):
        parts = [p for p in parts
                 if not re.match(r"(?i)(?:new|next|upcoming|latest|final)\s", p.group(0))]
    words = []
    for part in parts:
        word = part.group(0)
        if re.match(r"(?i)s\d", word):
            word = re.sub(r"\s+", "", word).upper()
        else:
            word = re.sub(_NUM + r"$", lambda m: str(_number(m.group(0))), word.lower())
        if word not in words:
            words.append(word)
    return " ".join(words)


_TIME_PHRASES = (
    "today", "tonight", "yesterday", "this morning", "this week", "this weekend",
    "last night", "last week", "last weekend", "this month", "this year", "right now",
    "latest", "newest",
)


def carry_time_words(query, text):
    """Append the message's time phrases that query extraction dropped.

    "Did OpenAI release anything new today? Search online" extracts to
    "OpenAI release anything new"; Brave's freshness filter is read from the
    query, so the "today" has to travel with it.
    """
    query = str(query or "").strip()
    lowered_query = query.lower()
    lowered_text = str(text or "").lower()
    # A query that already has its time ("… visible in Scotland tonight") keeps
    # it: "today" from "work was mad today" earlier in the message narrowed the
    # search to the past day (25 Oct live sweep).
    if any(re.search(r"\b" + re.escape(phrase) + r"\b", lowered_query) for phrase in _TIME_PHRASES):
        return query
    # "the latest episode … season 2, episode 9": the number already says which
    # one, and "latest" would limit the provider to the past week.
    numbered = re.search(r"\b(?:season|series|episode)\s+\d", lowered_query)
    extra = [phrase for phrase in _TIME_PHRASES
             if re.search(r"\b" + re.escape(phrase) + r"\b", lowered_text)
             and phrase not in lowered_query
             and not (numbered and phrase in ("latest", "newest"))]
    return (query + " " + " ".join(extra)).strip() if query and extra else query


# ── Spoiler boundary ──────────────────────────────────────────────────────────
# When the user has said where they are in a series, search results about it
# can still describe later episodes. The boundary travels with the results so
# the reply stops at the user's position unless they ask for more.
_POSITION_RES = (
    re.compile(r"\bs(?:eason)?\s*0*(\d{1,2})\s*[,:]?\s*e(?:p(?:isode)?)?\s*0*(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\bseason\s+(" + _NUM + r")\b[^.?!\n]{0,20}?\bepisode\s+(" + _NUM + r")\b", re.IGNORECASE),
    re.compile(r"\bepisode\s+(" + _NUM + r")\b[^.?!\n]{0,20}?\b(?:of\s+)?season\s+(" + _NUM + r")\b",
               re.IGNORECASE),
)
_EPISODE_ONLY_RE = re.compile(
    r"\b(?:up\s+to|on|at|just\s+(?:watched|finished|saw)|finished|watched|halfway\s+through)\s+"
    r"(?:the\s+)?(?:episode|ep|chapter|book|part)\s+(\d{1,3})\b",
    re.IGNORECASE,
)
_NO_SPOILERS_RE = re.compile(
    r"\b(?:no\s+spoilers?|without\s+spoilers?|spoiler[-\s]free|don'?t\s+(?:spoil|tell\s+me\s+what\s+happens)|"
    r"haven'?t\s+(?:seen|watched|finished|read)\s+(?:it|the|that|past|beyond|after))\b",
    re.IGNORECASE,
)
_SPOILERS_OK_RE = re.compile(
    r"\b(?:spoilers?\s+(?:are\s+)?(?:fine|ok(?:ay)?|welcome)|i\s+don'?t\s+mind\s+spoilers?|"
    r"spoil\s+(?:it|away)|tell\s+me\s+what\s+happens)\b",
    re.IGNORECASE,
)


def _message_text(message):
    content = message.get("content", "") if isinstance(message, dict) else message
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content
                        if isinstance(p, dict) and p.get("type") == "text")
    return str(content or "")


def spoiler_boundary(user_texts):
    """One instruction line for the post-results rule, or "".

    `user_texts` are the user's own turns, oldest first (only the last 12 are
    read). The most recent statement wins: a stated position or a no-spoilers
    request sets the boundary; "spoilers are fine" lifts it.
    """
    position, avoid = None, False
    for text in list(user_texts or [])[-12:]:
        text = _message_text(text)
        if _SPOILERS_OK_RE.search(text):
            position, avoid = None, False
            continue
        for pattern in _POSITION_RES:
            match = pattern.search(text)
            if match:
                a, b = _number(match.group(1)), _number(match.group(2))
                season, episode = (b, a) if pattern is _POSITION_RES[2] else (a, b)
                position = f"season {season}, episode {episode}"
                break
        else:
            match = _EPISODE_ONLY_RE.search(text)
            if match:
                position = f"part or episode {int(match.group(1))}"
        if _NO_SPOILERS_RE.search(text):
            avoid = True
    if position:
        return (f"Spoiler boundary: the user is up to {position}. Do not reveal anything that "
                "happens after that point, even if the results describe it. If later events "
                "matter, say there is more without saying what, unless the user asks for it.")
    if avoid:
        return ("Spoiler boundary: the user does not want spoilers. Do not reveal plot events, "
                "twists or endings from the results unless the user asks for them.")
    return ""


# How the reply should use the results. RESULTS_RULE alone read as a brief for
# a research answer: on 25 Oct (09:36) Sol, asked "have you seen what's going on
# with the next season of Silo?", answered with six paragraphs of release dates,
# plot, cast and "according to Wikipedia". The search informs the character; it
# does not replace it, and the depth follows what the user asked for.
VOICE_RULE = (
    "Reply as yourself: the same character, voice, humour and warmth as the rest of this "
    "conversation, following your character instructions. The results are information for "
    "you, not the reply itself."
)
CONVERSATIONAL_RULE = (
    "The user is chatting, not asking for a report: respond to what they actually said, as "
    "you normally would, and work in only the facts that answer it, in a few natural "
    "sentences. Do not summarise everything the results contain or add plot details, "
    "spoilers, cast lists or source names they did not ask about; name a source only when it "
    "matters, such as when sources disagree."
)
RESEARCH_RULE = (
    "The user asked for details: give a complete, well-organised answer, with structure where "
    "it helps, and say which source each key claim comes from."
)
# Words that ask for a researched answer rather than a chat about the topic.
_RESEARCH_RE = re.compile(
    r"\b(?:details?|detailed|in\s+depth|in-depth|thorough(?:ly)?|comprehensive|everything\s+(?:you\s+can|about|on|there\s+is)|"
    r"rundown|run-down|breakdown|overview|summary|summari[sz]e|recap|timeline|"
    r"compare|comparison|pros\s+and\s+cons|research|"
    r"sources?|cite|citations?|list\s+(?:of|all|the|every)|step[\s-]+by[\s-]+step|specs|specifications|"
    r"explain\s+(?:how|why|what|the))\b",
    re.IGNORECASE,
)


def research_request(text):
    """True when the user's own words ask for a detailed, researched answer.

    Read on the typed request without code, quotes or pasted material
    (request_text), so a shard or quotation cannot change the reply's depth.
    """
    return bool(_RESEARCH_RE.search(request_text(text)))


def results_rule(messages=None, request=None):
    """The instruction that follows a results block.

    RESULTS_RULE (grounding), VOICE_RULE, then, when the user's typed `request`
    is given, RESEARCH_RULE or CONVERSATIONAL_RULE, then any spoiler boundary
    stated in the user's turns.
    """
    users = [m for m in (messages or []) if isinstance(m, dict) and m.get("role") == "user"]
    lines = [RESULTS_RULE, VOICE_RULE]
    if request is not None:
        lines.append(RESEARCH_RULE if research_request(request) else CONVERSATIONAL_RULE)
    boundary = spoiler_boundary(users)
    if boundary:
        lines.append(boundary)
    return "\n".join(lines)


# ── Search tags as data ─────────────────────────────────────────────────────
# 24 Oct, 23:24: an OpenAI model writing ChatML shards about search behaviour
# wrote "[WEB SEARCH: …]" as the training target inside a ```chatml block.
# The tag detector saw it, stopped the reply mid-shard and ran a real Brave
# search. A tag is a request only when the model says it as itself; inside
# code, quoted, or once the reply is writing training material (a ChatML
# transcript or CX/DPO fields), it is data and passes through untouched.
TAG_OPEN_RE = re.compile(r"\[\s*WEB\s+SEARCH\s*:", re.IGNORECASE)
_TRAINING_MATERIAL_RE = re.compile(
    r"(?im)<\|im_start\|>|⟦im_start⟧|^[ \t>*#-]*(?:instruction|prompt|response|chosen|rejected)[ \t]*\**[ \t]*:"
)
_QUOTE_BEFORE = "\"'“‘`«"


def tag_is_data(text, pos):
    """True when a search tag starting at `pos` in model output is data, not a request."""
    before = str(text or "")[:pos]
    fences = before.count("```") + before.count("~~~")
    if fences % 2:
        return True
    line = before[before.rfind("\n") + 1:].replace("```", "")
    if line.count("`") % 2:
        return True
    if before.rstrip(" ")[-1:] in _QUOTE_BEFORE and before.rstrip(" "):
        return True
    return bool(_TRAINING_MATERIAL_RE.search(before))


def find_search_tag(text, start=0):
    """The first "[WEB SEARCH:" opening at or after `start` that is a request, or None."""
    text = str(text or "")
    for match in TAG_OPEN_RE.finditer(text, start):
        if not tag_is_data(text, match.start()):
            return match
    return None


def find_closed_search_tag(text, start=0):
    """(open_start, query, close_end) for the first closed request tag, or None.

    Brackets inside the query nest, so the tag closes at its own "]".
    """
    match = find_search_tag(text, start)
    if not match:
        return None
    depth, i = 1, match.end()
    while i < len(text):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return match.start(), text[match.end():i].strip(), i + 1
        i += 1
    return None


# ── Result markers in a post-search answer ──────────────────────────────────
# 26 Oct, Aurora on Gemma 4: after a real search the answer sometimes echoed
# HWUI's own block markers or wrote a results block of its own ("[WEB SEARCH
# RESULTS]" then invented prose, "[WEB SEARCH_QUERY: …]", "[WEB_RESULT_1]",
# "[WEB SOURCES]"). Whatever follows such a marker is an echo of the evidence
# or invented evidence, never the reply. Like tags, markers in code, quotes or
# training material are data.
RESULTS_MARKER_RE = re.compile(
    r"\[\s*(?:END\s+)?WEB[\s_]*(?:SEARCH[\s_]*(?:RESULTS?|QUERY)|SOURCES?|RESULTS?)(?![A-Za-z])",
    re.IGNORECASE,
)
_RESULTS_MARKER_STARTS = ("[WEB SEARCH RESULT", "[WEB SEARCH QUERY", "[WEB SOURCE", "[WEB RESULT",
                          "[END WEB SEARCH RESULT")


def find_results_marker(text, start=0):
    """Start of the first result marker at or after `start` that is not data, or None."""
    text = str(text or "")
    for match in RESULTS_MARKER_RE.finditer(text, start):
        if not tag_is_data(text, match.start()):
            return match.start()
    return None


def results_marker_hold_start(text, start=0):
    """Index of an unfinished trailing "[" that could still become a result marker, else len(text).

    A streamed answer holds this tail back until the next chunk shows what it is.
    """
    text = str(text or "")
    at = text.rfind("[", start)
    if at < 0:
        return len(text)
    tail = re.sub(r"[\s_]+", " ", text[at:]).upper()
    if "]" in tail or "\n" in text[at:] or len(tail) > 32:
        return len(text)
    if any(marker.startswith(tail) or tail.startswith(marker) for marker in _RESULTS_MARKER_STARTS):
        return at
    return len(text)


_REQUEST_TEXT_DROPS = (
    re.compile(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)", re.DOTALL),          # fenced code
    # inline code quoting a tag or ChatML (a backticked query is still a query)
    re.compile(r"`[^`\n]*(?:\[\s*WEB\s+SEARCH|<\|im_|⟦im_)[^`\n]*`", re.IGNORECASE),
    re.compile(r"<\|im_start\|>.*?(?:<\|im_end\|>|\Z)|⟦im_start⟧.*?(?:⟦im_end⟧|\Z)", re.DOTALL),
    re.compile(r"(?im)^[ \t>*#-]*(?:instruction|prompt|response|chosen|rejected)[ \t]*\**[ \t]*:.*$"),
    re.compile(r"\[\s*WEB\s+SEARCH\s*:[^\]\n]*\]?", re.IGNORECASE),        # a quoted tag
)


def request_text(text):
    """The user's own words for search routing: code, quoted tags and pasted
    training material blanked, so "train it to emit `[WEB SEARCH: …]`" or a
    pasted shard whose prompt says "search online" is not a search request.

    Blanked with spaces of the same length, so match positions still index
    the original text (the local path's self-reference checks rely on them).
    """
    text = str(text or "")
    for pattern in _REQUEST_TEXT_DROPS:
        text = pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return text


# ── Declined lookups ─────────────────────────────────────────────────────────
# "I don't think you want to look that up", "no need to search", "don't google
# it" decline a search, yet every one contains a request phrase, and with
# automatic search on the model's own tag was honoured on them too (25 Oct
# corpus). A negation in the same clause as a lookup verb declines it, unless
# the negation asks ("why don't you…", "don't you want to…", "can you not
# just…", "don't forget to…") or negates something else ("I don't know, can
# you…", "not sure", "no idea").
_LOOKUP_VERB_RE = re.compile(
    r"\b(?:look(?:ing|ed)?\s+(?:(?:it|that|this|them|those|these)\s+)?up\b(?!\s+(?:to|at)\b)|"
    # "search" as a noun or a subject is the topic, not an action: "without a
    # search tag", "when web search is necessary" (shard requests, 24 Oct).
    r"search(?:ing|ed|es)?\b(?!\s+(?:results?|engines?|history|bar|box|tags?|tools?|terms?|quer(?:y|ies)|"
    r"functions?|features?|buttons?|toggles?|decisions?|behaviou?r|capabilit(?:y|ies)|calls?|"
    r"is|was|are|were|should|would|will|might|may|needs?)\b)|googl(?:e|ing|ed)\b|"
    r"(?:check|look)(?:ing)?\s+online\b)",
    re.IGNORECASE,
)
_NEGATOR_RE = re.compile(
    r"\b(?:don'?t|do\s+not|doesn'?t|didn'?t|not|never|no|without|shouldn'?t|needn'?t|stop|quit)\b",
    re.IGNORECASE,
)
_CLAUSE_BREAK_RE = re.compile(r"[.!?;,:]|\b(?:but|anyway|actually|instead)\b", re.IGNORECASE)


def _negation_asks(clause, negation):
    before, after = clause[:negation.start()], clause[negation.end():]
    word = negation.group(0).lower()
    if re.search(r"\b(?:why|how\s+come)\b", before, re.IGNORECASE):
        return True                                        # why don't you look it up
    if re.match(r"\s+you\b", after, re.IGNORECASE) and word.startswith(("don", "do", "did")):
        return True                                        # don't you want to look it up
    if word == "not" and re.search(r"\b(?:can|could|will|would)\s+you\s*$", before, re.IGNORECASE):
        return True                                        # can you not just look it up
    if re.match(r"\s+(?:forget|hesitate|be\s+afraid|be\s+shy)\b", after, re.IGNORECASE):
        return True                                        # don't forget to look it up
    return bool(re.match(r"\s+(?:know|sure|certain|idea|remember|clue)\b", after, re.IGNORECASE))


def declined_lookup(text):
    """True when the user's own words decline a lookup this turn.

    The last lookup verb in the typed request (request_text) decides, so
    "don't look it up yet… actually, search online for it" searches.
    """
    text = request_text(text)
    verbs = list(_LOOKUP_VERB_RE.finditer(text))
    if not verbs:
        return False
    verb = verbs[-1]
    breaks = list(_CLAUSE_BREAK_RE.finditer(text, 0, verb.start()))
    clause = text[breaks[-1].end() if breaks else 0:verb.start()]
    negations = list(_NEGATOR_RE.finditer(clause))
    return bool(negations) and not _negation_asks(clause, negations[-1])


# ── Evidence checks ──────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]{1,200}>")


def clean_snippet(text):
    """Provider text as plain words: HTML entities decoded, markup removed.

    Brave returns "&#8216;Dark Matter&#8217;" and "<strong>How to Build the
    Box</strong>"; the model was reading the markup as part of the evidence.
    """
    import html
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html.unescape(str(text or "")))).strip()


def unsupported_names(reply, *sources):
    """Capitalised names in `reply` that appear in none of `sources`.

    A review pointer for diagnostics, not a verdict: a name can be common
    knowledge, and a supported name can still carry a wrong claim. On 25 Oct
    GPT-4o's reply about Dark Matter came from another show's result; checked
    against the right evidence, its "kitchen" scene would have had nothing to
    match.
    """
    haystack = " ".join(str(s or "") for s in sources).lower()
    found = []
    for match in _TITLE_RE.finditer(str(reply or "")):
        name = re.sub(r"['’]s$", "", match.group(0).strip())      # "Steam’s" is "Steam"
        words = [w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", name)]
        words = [w for w in words if len(w) >= 3 and w not in _STOPWORDS]
        if words and any(w not in haystack for w in words) and name not in found:
            found.append(name)
    return found


_PROVIDER_ERROR_RE = re.compile(r"\s*(?:\[(?:OpenAI|Anthropic) error\b|⚠️ (?:OpenAI|Anthropic) model error)")


def provider_error_reply(text):
    """True when a cloud "reply" is the provider's error text, not an answer."""
    return bool(_PROVIDER_ERROR_RE.match(str(text or "")))


# ── Per-turn search authorisation (every route) ─────────────────────────────
# A model may PROPOSE a search: a [WEB SEARCH: …] tag, a search control, or the
# intent gate's "SEARCH: …" line. Whether a proposal runs is decided per turn,
# the same way on the native, raw ChatML and cloud paths, from the user's own
# request and the real conversation only: never from the system prompt, the
# style examples or the card. Automatic search being switched on is not, by
# itself, permission: on 26 Oct (Personal, GPT-6.1 Sol) "fhfh" after a chat
# about age and wisdom ran a model-written search about Florida fictitious-name
# law, built from a style example's mafia cat, because the native path honoured
# any model tag whenever automatic search was on.

PROPOSAL_WITHHELD_NOTICE = (
    "\n\n*🔍 No web search was run — the search the reply asked for didn’t match your message.*"
)

# Words that make a request lean on an earlier turn for its subject.
_BACK_REFERENCE_RE = re.compile(
    r"\b(?:it|its|that|this|those|these|they|them|their|he|she|him|her|his|there|"
    r"the\s+same|the\s+(?:show|series|film|movie|game|book|album|song|episode|season|one|thing|story|"
    r"article|news|update|release|launch|trailer|finale|new\s+one|next\s+one|last\s+one)|"
    r"what\s+about|how\s+about)\b",
    re.IGNORECASE,
)
_HISTORY_TURNS = 6   # the recent real turns a back-reference can resolve to


def refers_back(text):
    """Does this request take its subject from an earlier turn?

    True when it names nothing topical of its own ("Search online then"),
    names an instalment without a title ("when does season 3 come out?"), or
    points back with a pronoun or "the show"/"what about". "fhfh" does not.
    """
    text = request_text(text)
    if not _terms(text):
        return True
    if instalment_words(text) and not instalment_topic(text):
        return True
    return bool(_BACK_REFERENCE_RE.search(text))


def _history_text(history):
    turns = [m for m in (history or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    return " ".join(strip_search_ui(_message_text(m)) for m in turns[-_HISTORY_TURNS:])


def query_alignment(query, request, history=(), names=()):
    """``(aligned, basis)``: is a proposed query about what the user asked?

    The query's subject words (instalment words alone name no subject) must
    appear in the request, or, only when the request refers back, in the last
    few real turns of `history` (user/assistant messages, never the system
    prompt or style examples). Names of the user and the character are never
    a subject. `basis` is "request", "history", or why it failed.
    """
    name_terms = _terms(" ".join(str(n or "") for n in names), query=False)
    query_terms = _terms(query) - name_terms
    subject = {term for term in query_terms if not _instalment_term(term)} or query_terms
    if not subject:
        return False, "no_topic"
    request = request_text(request)
    request_terms = _terms(request, query=False) - name_terms
    if any(_matches(term, request_terms) for term in subject):
        return True, "request"
    if not refers_back(request):
        return False, "not_in_request"
    history_terms = _terms(_history_text(history), query=False) - name_terms
    if any(_matches(term, history_terms) for term in subject):
        return True, "history"
    return False, "not_in_request_or_history"


class SearchTurn:
    """One turn's search authority, decided once and shared by every check on that turn.

    `explicit`: the user asked for a lookup this turn (the caller's opt-in test).
    `automatic`: automatic current-topic search is switched on for this turn.
    The switch alone authorises nothing: a proposal also needs the request to
    name a current topic (current_topic_signal), or to be a question that
    refers back to the conversation.
    """

    def __init__(self, request, explicit=False, automatic=False, history=(), names=()):
        self.request = str(request or "")
        text = request_text(self.request)
        self.declined = declined_lookup(self.request)
        self.explicit = bool(explicit) and not self.declined
        self.automatic = bool(automatic) and not self.declined
        self.current_topic = self.automatic and bool(
            current_topic_signal(text) or (refers_back(text) and _ASKS_RE.search(text)))
        self.history = list(history or ())
        self.names = tuple(n for n in names or () if n)

    @property
    def may_propose(self):
        """Whether a model proposal can be considered at all this turn."""
        return self.explicit or self.current_topic

    def authorise(self, query):
        """``(allowed, reason)`` for one proposed query, checked before any retrieval."""
        if self.declined:
            return False, "declined"
        if not self.may_propose:
            return False, "not_current_topic" if self.automatic else "not_requested"
        if not str(query or "").strip():
            return False, "no_query"
        if self.explicit and self._open_delegation():
            return True, "delegated"
        return query_alignment(query, self.request, self.history, self.names)

    def _open_delegation(self):
        """An explicit request naming nothing ("Can you do a web search?") with no
        topical recent turns: the user left the topic to the model, so there is
        nothing to check its query against. "fhfh" names something; a request
        after a real conversation is checked against that conversation."""
        name_terms = _terms(" ".join(self.names), query=False)
        return (not (_terms(request_text(self.request)) - name_terms)
                and not (_terms(_history_text(self.history)) - name_terms))
