from __future__ import annotations

import re

# Unicode escapes keep this rule table stable on Windows code-page shells.
CHARACTERS = (
    "\u9ec4\u4ea6\u73ab", "\u73ab\u7470", "\u9ec4\u632f\u534e", "\u767d\u6653\u8377",
    "\u5e84\u56fd\u680b", "\u82cf\u66f4\u751f", "\u59dc\u96ea\u743c", "\u97e9\u9e66",
)

ACTION_VOCAB = {
    "\u6454\u5012": "\u6454\u5012 \u5d34\u811a \u5d34\u4e86\u811a \u626d\u4f24 \u811a\u75bc \u4e0d\u80fd\u52a8 \u5012\u5730 \u8eba\u5730 \u54ce\u5440 \u6551\u63f4 \u67e5\u770b\u5012\u5730\u8005",
    "\u8d70\u8def": "\u8d70\u8def \u884c\u8d70 \u5f80\u524d\u8d70 \u5c71\u8def \u5f92\u6b65 \u4e0b\u5c71 \u8d70\u5eca",
    "\u8bf4\u8bdd": "\u8bf4\u8bdd \u5f00\u53e3 \u8be2\u95ee \u56de\u7b54 \u544a\u8bc9 \u804a\u5929 \u4ea4\u8c08 \u5bf9\u767d \u5b57\u5e55",
    "\u8868\u767d": "\u8868\u767d \u9080\u8bf7 \u89c1\u7236\u6bcd \u89c1\u7238\u5988 \u559c\u6b22 \u771f\u5fc3 \u5973\u53cb \u62d2\u7edd \u6c89\u9ed8",
    "\u62c9\u624b": "\u62c9\u624b \u62c9\u4eba\u5bb6\u624b \u7275\u624b \u9760\u8fd1 \u8ddd\u79bb \u7f29\u77ed\u8ddd\u79bb \u6293\u624b \u63a8\u5230\u4e00\u8d77 \u5f80\u4e00\u8d77\u63a8 \u5236\u9020\u673a\u4f1a \u6559\u54e5\u54e5 \u6559\u8ffd\u4eba \u8ffd\u4eba",
    "\u7167\u987e": "\u7167\u987e \u6400\u6276 \u6276\u7740 \u62d0\u6756 \u6c34\u679c \u524a\u6c34\u679c \u62ff\u4e1c\u897f \u817f\u4f24",
    "\u5403\u996d": "\u5403\u996d \u5403\u4e1c\u897f \u996d\u5c40 \u9910\u684c \u7897 \u7b77\u5b50 \u5939\u83dc",
    "\u559d\u6c34": "\u559d\u6c34 \u676f\u5b50 \u6c34\u676f \u9012\u6c34 \u5582\u6c34",
    "\u5de5\u4f5c": "\u5de5\u4f5c \u8003\u5bdf \u89c2\u5bdf \u5206\u6790 \u753b\u5eca \u706f\u5149 \u91c7\u5149 \u8d27\u68af \u5c55\u89c8 \u8f6c\u6b63",
    "\u4e89\u5435": "\u4e89\u5435 \u5435\u67b6 \u79bb\u5a5a \u7236\u6bcd \u5bb6\u91cc \u611f\u60c5\u98ce\u66b4",
}


def parse_intent(text: str, *, previous_subject: str = "") -> dict:
    text = str(text or "").strip()
    people = [name for name in CHARACTERS if name in text]
    subject = people[0] if people else ""
    pronouns = r"(?:^|[\uFF0C,\u3002\uFF1B;])?(?:\u4ed6|\u5979|\u5bf9\u65b9|\u7537\u4eba|\u5973\u4eba)"
    if not subject and previous_subject and re.search(pronouns, text):
        subject = previous_subject
    actions = [name for name, aliases in ACTION_VOCAB.items()
               if name in text or any(term in text for term in aliases.split())]
    speaking_words = ("\u8bf4", "\u95ee", "\u5f00\u53e3", "\u56de\u5e94", "\u529d")
    state = "speaking" if any(word in text for word in speaking_words) else "acting"
    expanded = " ".join([text, subject, *people, *actions,
                         *(ACTION_VOCAB[action] for action in actions)]).strip()
    return {"text": text, "subject": subject, "characters": people,
            "actions": actions, "state": state, "expanded_query": expanded}
