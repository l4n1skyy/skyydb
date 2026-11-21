"""Auto-classify eLearn items (stream items, announcements, content, gradebook columns) by
what they are, and how much they matter. Rules are plain regexes over the title (and, for
announcements, the start of the body), checked top to bottom; the first match wins.

Importance:  1 = act on it (deadlines, grades, tests, assignments, submissions, exams)
             2 = affects your week (class schedule / venue / attendance)
             3 = context (new content, welcome notes, group-finding)
             4 = optional (events, internships, talks, competitions)
             5 = noise (old COVID notices, campus admin)
"""
import re

R = lambda p: re.compile(p, re.I)

# Where it came from matters as much as the words: a module's own page (2701-CSC3034 ...) is
# about that module; the faculty / school / career / internship pages are institution-wide
# broadcasts. A module is recognised by the year-intake code in front of its name.
MODULE = R(r"^\d{4,6}(_\d{6})?-[A-Z]{3}\d{4}")


def is_module(course):
    return bool(MODULE.match(course or ""))


# (category, importance, title pattern): checked top to bottom, first match wins
GRADES = ("grade", 1, R(r"^grade posted|grades? (have been |has been |are |is )?(released|posted|published|updated|available)|"
                        r"(preliminary |provisional )?(scores?|marks?|results?)\b.*\b(released|posted|published|available)|marks? posted|"
                        r"\bgradebook\b|\bCA\b.*\bscores?\b|exam (cycle )?results?|results? released|feedback (is |has been )?(now )?(available|released)"))
DEADLINES = ("deadline", 1, R(r"^due:|\bdue date\b|\bdeadline\b|\bdateline\b|extension\b|extended (to|until)|submit (by|before|on)|please submit|"
                            r"submission (date|deadline|link|page|box)|pending (tutorial|practical|submission)|not yet (formed|submitted)"))
EXAMS = ("exam", 1, R(r"\b(final|mid[- ]?term|midterm|resit|take[- ]home)\s+(exam|examination|test|paper|assessment)|\bexam(ination)?s?\b|"
                      r"formula sheet|revision (class|session)|case study test|lockdown browser|respondus|invigilat|seating"))
ASSESS = ("assessment", 1, R(r"\b(test|quiz|assignment|project|proposal|presentation|coursework|\bCW\b|case study|assessment|pitching|viva|"
                             r"take-home activity|checkpoint)\b"))
CLASS = ("class", 2, R(r"\bno (lecture|class|tutorial|practical|lab)|cancel+ed|replace(d|ment)|re-?schedul|venue|changed? (to|of)|class (will be|on)|"
                       r"online (class|lecture|session)|compulsory|must attend|attendance|week\s*\d+|schedule|practical (group|class)|"
                       r"tutorial (group|class)|\bgroups?\b|timetable|consultation|lab usage|relocation|time ?table"))
TEAM = ("team", 3, R(r"teammate|team ?mate|looking for (a )?team|find(ing)? (a )?team|group (members?|formation|list)|name list|helping student"))
WELCOME = ("welcome", 3, R(r"^welcome|about this course|course (outline|information|introduction)|getting started|hello|^introduction"))
# internships and jobs get their own section; competitions, scholarships and the like stay "opportunity"
INTERNSHIP = ("internship", 4, R(r"intern(ship)?s?\b|\bjobs?\b|hiring|recruit|vacanc|trainee|graduate (programme|program)|freelance|"
                                r"part[- ]?time|full[- ]?time|career (fair|talk|day)|walk-?in|we are looking for|job (opening|opportunit)"))
OPPORTUNITY = ("opportunity", 4, R(r"hackathon|competition|challenge|career|scholarship|\bfair\b|talent|opportunit|apply (now|by)|"
                                  r"applications? (now )?open|call for|ambassador|programme 20|startup|ilabs|\bCTF\b|capture the flag"))
EVENT = ("event", 4, R(r"workshop|webinar|seminar|\btalk\b|speaker|series|invitation|invites?|join us|register|registration|sign up|"
                       r"\bclub\b|society|council|run\b|fest|summit|conference|forum|expo|open day|carnival|orientation|charity|volunteer|"
                       r"campaign|survey|evaluation|celebrat|tour\b|meet ?(&|and) ?greet|award|tea session|demo day|speaker"))
NOTICE = ("notice", 5, R(r"covid|health advisory|advisory\b|\bMCO\b|e-health|fraud|scam|phishing|suspicious|maintenance|downtime|library|parking|"
                         r"shuttle|ID card|password|printing|wi-?fi|policy|notice|holiday|closure|student representatives|iZone|enrol+ment|"
                         r"subject enrol|cohort|declaration|vaccin|safety"))

MODULE_RULES = [GRADES, DEADLINES, EXAMS, ASSESS, CLASS, TEAM, WELCOME, INTERNSHIP, EVENT, OPPORTUNITY]
# Institution-wide pages: exam timetables and results still matter; the rest is optional or noise
ORG_RULES = [GRADES, ("exam", 1, R(r"resit|exam(ination)? (timetable|schedule|cycle|result)|final exam")), NOTICE, INTERNSHIP, OPPORTUNITY, EVENT,
             ("class", 2, R(r"timetable|venue change|class relocation"))]

# What a file added to a course is about: these are all importance 3 ("content"), only the label differs
TOPICS = (
    ("recording", R(r"recording|video|panopto|zoom|youtube|\.mp4")),
    ("assessment-file", R(r"assignment|brief|rubric|marking|proposal|template|submission|quiz|test|exam|past year|sample question")),
    ("practical", R(r"practical|tutorial|lab\b|exercise|worksheet|workshop|dataset|source files|sample input|solution")),
    ("lecture", R(r"lecture|slides|handout|chapter|topic|week|notes|\bppt|introduction|overview|reading")),
)

# Gradebook columns: major pieces of assessment against routine ones
MAJOR = R(r"assignment|test|exam|project|proposal|report|presentation|\bCW\b|case study|pitch|viva|mid-?term|final|\bCA\b|overall")
MINOR = R(r"checkpoint|pre[- ]class|h5p|attendance|survey|feedback|orientation|password|printing|virtual|user services|activity|practice|testing")


def clean(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def classify(title, body="", course=""):
    """-> (category, importance) for a stream item / announcement"""
    title, body = clean(title), clean(body)[:400]
    if title.startswith("Added:"):
        return "content", 3
    if title.startswith(("New course available", "New organization available")):
        return "enrolment", 3
    module = is_module(course) or not course
    for cat, imp, pat in (MODULE_RULES if module else ORG_RULES):
        if pat.search(title):
            return cat, imp
    if module:  # nothing in the title: a strong signal in the first lines of the body
        for cat, imp, pat in (GRADES, DEADLINES, EXAMS):
            if pat.search(body):
                return cat, imp
        return "info", 3
    if re.search(r"intern|career", course, re.I):  # a page that is all internships: anything unmatched there is about them
        return "internship", 4
    return "general", 4


def topic(filename):
    return next((name for name, pat in TOPICS if pat.search(filename)), "file")


def column_weight(name, possible=None):
    """Gradebook column -> 'major' or 'minor'"""
    if MINOR.search(name) and not re.search(r"assignment|exam|mid-?term|final", name, re.I):
        return "minor"
    return "major" if MAJOR.search(name) else "minor"
