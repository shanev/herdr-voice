"""How herdr-voice names and describes an agent, in one place for the skill's vux_state.py and the desk-alerts plugin
(which imports this file from the skill's folder beside it in the repo herdr installs).

An agent's name is its herdr agent name, the one handle the user, the skill and Vux use for it. Agents started by
voice get one from the request (SKILL.md, **Start a session**); the desk-alerts plugin names an agent started at the
desk once, from its first task, with `name_for`. Until then it's `placeholder`. Pane and tab labels are never read:
they belong to the user and to plugins such as herdr.auto-title, which rewrites them as the work changes.

Stdlib only.
"""
import re
import unicodedata

# herdr's rule for agent names ("Names must match [a-z][a-z0-9_-]{0,31} and be unique among live agents").
NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
NAME_LENGTH = 32
# Room left for "-2", "-3"… on a name that's taken.
BASE_LENGTH = NAME_LENGTH - 4
# Words a name made from a task keeps, at most: a short phrase the user would say.
NAME_WORDS = 4
# Small words a name drops ("Tesla Roadster physics simulation for the demo" is tesla-roadster-physics).
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with"}
# How each agent kind is written, where that isn't its kind capitalized (herdr agent start --help lists the kinds).
KIND_NAMES = {"omp": "omp", "pi": "pi", "agy": "Antigravity", "opencode": "OpenCode", "qodercli": "Qoder",
              "mastracode": "Mastra Code"}
# An agent with no task yet.
NO_TASK = "New session"


def plain(text):
    """Text without icon-font glyphs (Nerd Fonts' private-use characters, which a phone can't show and speech would
    read), spaces folded."""
    kept = "".join(c for c in text or "" if not (0xE000 <= ord(c) <= 0xF8FF or ord(c) >= 0xF0000))
    return " ".join(kept.split())


def kind_name(kind):
    """An agent kind as people write it: Claude, Codex, omp."""
    return KIND_NAMES.get(kind) or (kind or "agent").capitalize()


def task_title(agent, workspace_label=None):
    """What the agent is working on: the title it gives its terminal, without the agent's own chrome around it (status
    glyphs, omp's "π > ", Grok's " - grok", Codex's " | <folder>"); "" when the title only names the agent or its folder
    (an agent that hasn't been given a task yet). Agents set it once per task, so it holds while the agent works."""
    kind = agent.get("agent") or ""
    folder = (agent.get("cwd") or "").rstrip("/").rsplit("/", 1)[-1]
    title = plain(agent.get("terminal_title_stripped") or agent.get("terminal_title"))
    # Status glyphs ahead of the title: spinners, ✳, bullets.
    title = title.lstrip("•· ").lstrip()
    while title and unicodedata.category(title[0]) == "So":
        title = title[1:].lstrip()
    title = re.sub(r"^π\s*[>›»:-]\s*", "", title)
    for tail in filter(None, {kind, kind_name(kind), folder}):
        title = re.sub(rf"\s+[-–—|·]\s+{re.escape(tail)}$", "", title, flags=re.I)
    generic = {kind, kind_name(kind), f"{kind_name(kind)} code", f"{kind} cli", folder, workspace_label or "", "~"}
    return "" if title.lower() in {g.lower() for g in generic} else title


def task(agent, workspace_label=None):
    """The item's task: what the agent is on, or "New session" before it has a task."""
    return task_title(agent, workspace_label) or NO_TASK


def placeholder(pane):
    """The name of an agent that has none yet: `new-<pane>`, its pane id lowercased with `:` as `-` (`w1P:p8` is
    `new-w1p-p8`). Pane ids are never reused, so no other agent has it."""
    return "new-" + pane.lower().replace(":", "-")


def name(agent):
    """The agent's name: its herdr agent name, else `placeholder`."""
    return agent.get("name") or placeholder(agent["pane_id"])


def words(text):
    """The words of a text, lowercase ASCII letters and digits ("Today's" is "todays", "Café" is "cafe")."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", re.sub(r"['’]", "", ascii_text))


def slug(task_text, kind=None):
    """A herdr agent name for a task, the way the skill names a session it starts: a short kebab-case phrase from the
    task, lowercase, at most four words, small words left out, short enough for "-2" to fit herdr's 32 characters.
    It starts with a letter (herdr's rule): a task that starts with a digit gets the agent's kind first
    ("1Password service account" is claude-1password-service-account, cut to fit). None when the task has no words."""
    kept = [w for w in words(task_text) if w not in SMALL_WORDS] or words(task_text)
    if kept and not kept[0][0].isalpha():
        kept = [*words(kind_name(kind)), *kept]
    kept = kept[:NAME_WORDS]
    while len(kept) > 1 and len("-".join(kept)) > BASE_LENGTH:
        kept.pop()
    base = "-".join(kept)[:BASE_LENGTH].rstrip("-")
    return base if base and NAME.fullmatch(base) else None


def unique(base, taken):
    """`base`, else `base-2`, `base-3`… : the first that no live agent has (herdr's names are unique on the server)."""
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"


def base_name(agent, workspace_label=None):
    """The name an unnamed agent gets once it has a task, before `unique`: its task as `slug` says (or `placeholder`
    for a task with no words to name it by); None while it has no task (it stays unnamed, `placeholder` to Vux)."""
    doing = task_title(agent, workspace_label)
    if not doing:
        return None
    return slug(doing, agent.get("agent")) or placeholder(agent["pane_id"])


def name_for(agent, taken, workspace_label=None):
    """`base_name`, unique among `taken`."""
    base = base_name(agent, workspace_label)
    return None if base is None else unique(base, taken)
