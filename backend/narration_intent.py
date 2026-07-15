from __future__ import annotations

import re

# Unicode escapes keep this rule table stable on Windows code-page shells.
CHARACTERS = (
    "\u9ec4\u4ea6\u73ab", "\u73ab\u7470", "\u9ec4\u632f\u534e", "\u767d\u6653\u8377",
    "\u5e84\u56fd\u680b", "\u82cf\u66f4\u751f", "\u65b9\u534f\u6587", "\u59dc\u96ea\u743c", "\u97e9\u9e66",
)

ACTION_VOCAB = {
    "\u6454\u5012": "\u6454\u5012 \u5d34\u811a \u5d34\u4e86\u811a \u626d\u4f24 \u811a\u75bc \u4e0d\u80fd\u52a8 \u5012\u5730 \u8eba\u5730 \u54ce\u5440 \u6551\u63f4 \u67e5\u770b\u5012\u5730\u8005",
    "\u8d70\u8def": "\u8d70\u8def \u884c\u8d70 \u5f80\u524d\u8d70 \u5c71\u8def \u5f92\u6b65 \u4e0b\u5c71 \u8d70\u5eca",
    "\u8bf4\u8bdd": "\u8bf4\u8bdd \u5f00\u53e3 \u8be2\u95ee \u56de\u7b54 \u544a\u8bc9 \u804a\u5929 \u4ea4\u8c08 \u5bf9\u767d \u5b57\u5e55",
    "\u8868\u767d": "\u8868\u767d \u9080\u8bf7 \u89c1\u7236\u6bcd \u89c1\u7238\u5988 \u559c\u6b22 \u771f\u5fc3 \u5973\u53cb \u62d2\u7edd \u6c89\u9ed8",
    "\u62c9\u624b": "\u62c9\u624b \u62c9\u4eba\u5bb6\u624b \u7275\u624b \u9760\u8fd1 \u8ddd\u79bb \u7f29\u77ed\u8ddd\u79bb \u6293\u624b \u63a8\u5230\u4e00\u8d77 \u5f80\u4e00\u8d77\u63a8 \u5236\u9020\u673a\u4f1a \u6559\u54e5\u54e5 \u6559\u8ffd\u4eba \u8ffd\u4eba",
    "\u7167\u987e": "\u7167\u987e \u6400\u6276 \u6276\u7740 \u62d0\u6756 \u6c34\u679c \u524a\u6c34\u679c \u62ff\u4e1c\u897f \u817f\u4f24",
    "\u5403\u996d": "\u5403\u996d \u5403\u4e1c\u897f \u996d\u5c40 \u9910\u684c \u7897 \u7b77\u5b50 \u5939\u83dc",
    "\u505a\u996d": "\u505a\u996d \u4e0b\u53a8 \u7092\u83dc \u70e7\u83dc \u7aef\u83dc \u53a8\u623f \u7076\u53f0 \u6536\u62fe\u9910\u684c",
    "\u559d\u6c34": "\u559d\u6c34 \u676f\u5b50 \u6c34\u676f \u9012\u6c34 \u5582\u6c34",
    "\u5de5\u4f5c": "\u5de5\u4f5c \u8003\u5bdf \u89c2\u5bdf \u5206\u6790 \u753b\u5eca \u706f\u5149 \u91c7\u5149 \u8d27\u68af \u5c55\u89c8 \u8f6c\u6b63",
    "\u4e89\u5435": "\u4e89\u5435 \u5435\u67b6 \u79bb\u5a5a \u7236\u6bcd \u5bb6\u91cc \u611f\u60c5\u98ce\u66b4",
    "\u63a8\u5165\u6cf3\u6c60": "\u63a8\u5165\u6cf3\u6c60 \u63a8\u4e0b\u6cf3\u6c60 \u63a8\u8fdb\u6cf3\u6c60 \u63a8\u4e0b\u6c34 \u843d\u6c34 \u6389\u8fdb\u6cf3\u6c60 \u6c60\u8fb9\u63a8\u4eba",
    "\u770b\u76d1\u63a7": "\u770b\u76d1\u63a7 \u67e5\u770b\u76d1\u63a7 \u8c03\u76d1\u63a7 \u76d1\u63a7\u753b\u9762 \u76d1\u63a7\u5c4f\u5e55 \u76ef\u7740\u76d1\u63a7",
}

LOCATION_VOCAB = {
    "\u5c0f\u5c4b": "\u5c0f\u5c4b \u6728\u5c4b \u5c71\u95f4\u5c0f\u5c4b \u5c71\u91cc\u7684\u5c4b\u5b50 \u6797\u4e2d\u5c0f\u5c4b",
    "\u6cf3\u6c60": "\u6cf3\u6c60 \u6c60\u8fb9 \u6c34\u6c60 \u9732\u5929\u6cf3\u6c60",
    "\u76d1\u63a7\u5ba4": "\u76d1\u63a7\u5ba4 \u4fdd\u5b89\u5ba4 \u503c\u73ed\u5ba4 \u76d1\u63a7\u5899",
    "\u53a8\u623f": "\u53a8\u623f \u7076\u53f0 \u64cd\u4f5c\u53f0",
    "\u5bb6\u4e2d": "\u5bb6\u4e2d \u5bb6\u91cc \u5ba2\u5385 \u5367\u5ba4",
    "\u529e\u516c\u5ba4": "\u529e\u516c\u5ba4 \u516c\u53f8 \u5de5\u4f4d \u4f1a\u8bae\u5ba4",
}

OBJECT_VOCAB = {
    "\u76d1\u63a7\u5c4f\u5e55": "\u76d1\u63a7\u5c4f\u5e55 \u76d1\u63a7\u753b\u9762 \u76d1\u89c6\u5668 \u591a\u5bab\u683c\u753b\u9762",
    "\u624b\u673a": "\u624b\u673a \u7535\u8bdd \u624b\u673a\u5c4f\u5e55",
    "\u6587\u4ef6": "\u6587\u4ef6 \u5408\u540c \u7eb8\u5f20 \u8d44\u6599",
    "\u9c9c\u82b1": "\u73ab\u7470\u82b1 \u9c9c\u82b1 \u82b1\u675f \u6367\u82b1",
}

TEMPORAL_ACTIONS = {"\u6454\u5012", "\u8d70\u8def", "\u62c9\u624b", "\u63a8\u5165\u6cf3\u6c60"}

HARD_ACTION_TERMS = {
    "\u6454\u5012": "\u6454\u5012 \u5d34\u811a \u626d\u4f24 \u5012\u5730",
    "\u8d70\u8def": "\u8d70\u8def \u884c\u8d70 \u5f92\u6b65 \u4e0b\u5c71",
    "\u8868\u767d": "\u8868\u767d",
    "\u62c9\u624b": "\u62c9\u624b \u7275\u624b \u6293\u624b",
    "\u7167\u987e": "\u7167\u987e \u6400\u6276 \u6276\u7740 \u524a\u6c34\u679c",
    "\u5403\u996d": "\u5403\u996d \u5939\u83dc",
    "\u505a\u996d": "\u505a\u996d \u4e0b\u53a8 \u7092\u83dc \u70e7\u83dc \u7aef\u83dc \u6536\u62fe\u9910\u684c",
    "\u559d\u6c34": "\u559d\u6c34 \u9012\u6c34 \u5582\u6c34",
    "\u4e89\u5435": "\u4e89\u5435 \u5435\u67b6",
    "\u63a8\u5165\u6cf3\u6c60": "\u63a8\u5165\u6cf3\u6c60 \u63a8\u4e0b\u6cf3\u6c60 \u63a8\u8fdb\u6cf3\u6c60 \u63a8\u4e0b\u6c34",
    "\u770b\u76d1\u63a7": "\u770b\u76d1\u63a7 \u67e5\u770b\u76d1\u63a7 \u8c03\u76d1\u63a7 \u76ef\u7740\u76d1\u63a7",
}


def _vocab_hits(text: str, vocab: dict[str, str]) -> list[str]:
    return [name for name, aliases in vocab.items()
            if name in text or any(term in text for term in aliases.split())]


def _negative_requirements(text: str) -> list[str]:
    terms: list[str] = []
    for vocab in (ACTION_VOCAB, LOCATION_VOCAB, OBJECT_VOCAB):
        for name, aliases in vocab.items():
            candidates = [name, *aliases.split()]
            if any(re.search(rf"(?:\u4e0d\u662f|\u5e76\u975e|\u4e0d\u8981|\u6ca1\u6709|\u800c\u975e).{{0,4}}{re.escape(term)}", text)
                   for term in candidates):
                terms.append(name)
    return list(dict.fromkeys(terms))


def parse_intent(text: str, *, previous_subject: str = "") -> dict:
    text = str(text or "").strip()
    people = [name for name in CHARACTERS if name in text]
    subject = people[0] if people else ""
    pronouns = r"(?:^|[\uFF0C,\u3002\uFF1B;])?(?:\u4ed6|\u5979|\u5bf9\u65b9|\u7537\u4eba|\u5973\u4eba)"
    if not subject and previous_subject and re.search(pronouns, text):
        subject = previous_subject
    actions = _vocab_hits(text, ACTION_VOCAB)
    locations = _vocab_hits(text, LOCATION_VOCAB)
    objects = _vocab_hits(text, OBJECT_VOCAB)
    speaking_words = ("\u8bf4", "\u95ee", "\u5f00\u53e3", "\u56de\u5e94", "\u529d")
    state = "speaking" if any(word in text for word in speaking_words) else "acting"
    must_not_have = _negative_requirements(text)
    positive_actions = [item for item in actions if item not in must_not_have]
    hard_actions = [item for item in positive_actions
                    if any(term in text for term in HARD_ACTION_TERMS.get(item, "").split())]
    positive_locations = [item for item in locations if item not in must_not_have]
    positive_objects = [item for item in objects if item not in must_not_have]
    expanded = " ".join([
        text, subject, *people, *positive_actions, *positive_locations, *positive_objects,
        *(ACTION_VOCAB[action] for action in positive_actions),
        *(LOCATION_VOCAB[location] for location in positive_locations),
        *(OBJECT_VOCAB[obj] for obj in positive_objects),
    ]).strip()
    must_have = list(dict.fromkeys([*people, *hard_actions, *positive_locations, *positive_objects]))
    temporal_type = "action_sequence" if any(action in TEMPORAL_ACTIONS for action in hard_actions) else "single_frame"
    return {
        "text": text,
        "subject": subject,
        "characters": people,
        "actions": positive_actions,
        "locations": positive_locations,
        "objects": positive_objects,
        "state": state,
        "temporal_type": temporal_type,
        "must_have": must_have,
        "must_not_have": must_not_have,
        "hard_requirements": {
            "characters": people,
            "actions": hard_actions,
            "locations": positive_locations,
            "objects": positive_objects,
        },
        "requires_candidate_review": bool(
            bool(hard_actions) or len(people) >= 2 or positive_locations or positive_objects
        ),
        "expanded_query": expanded,
    }
