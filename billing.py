"""What the app says when a paid call stops working (#99).

The failure this exists for is **silence**. Every paid path here already
catches its own exception, logs it in full and shows the learner one calm
sentence -- `textgen.generate()`'s "The text could not be written just now",
`topicgen.propose()`'s return of nothing. That is right for almost everything
that goes wrong at Anthropic: a request id and a stack trace are not the
learner's business, and "please try again" is the useful instruction.

**An exhausted credit balance is the exception, because trying again is
exactly the wrong instruction.** It will not work in a minute or an hour, and
nothing else on the page looks broken -- the site keeps serving, the deck is
there, the games play. #99 was filed after that happened: the feature simply
stopped, politely, and said the same thing it says for a network blip.

So this module holds the one question -- *is this the money?* -- and the one
sentence that answers it, with the address to write to. One declaration, in
the repo's usual shape, because the next paid call (`topicgen`, and whatever
follows it) should ask the same question rather than inventing a second answer.

**Detection is by message, not by exception type, and that is deliberate.**
Anthropic reports an exhausted balance as an ordinary `BadRequestError` whose
*text* names the balance; there is no distinct class to catch and no balance
endpoint to poll. ai_agent's `api_error_response()` has read it the same way
since long before this module, and the phrases here are its phrases -- the two
repos are answering one question about one provider, so they agree on purpose.
If Anthropic ever changes the wording, this fails **back to the generic
sentence**, which is the old behaviour rather than a new bug.

There is no proactive check, and the ticket's first line asks for one. A
balance cannot be read without spending a call, so "confirm the credits on
entry" would mean paying for every page load to learn something that only
matters when somebody actually uses a paid feature -- and would still miss the
balance running out one minute later. Catching the failure where it already
happens costs nothing per visit and cannot go stale.
"""

import os

# Overridable so a fork does not write to this address, defaulted so the
# sentence is never half a sentence on a deployment that forgot to set it.
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "anton.kuznietsov@gmail.com")

CREDIT_PHRASES = ("credit balance", "insufficient credits",
                  "insufficient funds", "billing")

GENERIC_NOTICE = "It could not be done just now. Please try again."


def out_of_credit(error):
    """Whether this failure is the Anthropic balance rather than bad luck.

    Takes the exception itself, so a caller that already holds one does not
    have to stringify it first, and reads it as text for the reason above.
    """
    if not error:
        return False
    text = str(error).casefold()
    return any(phrase in text for phrase in CREDIT_PHRASES)


def credit_notice():
    """The sentence a learner sees when the balance is gone.

    It says what happened, that waiting will not fix it, and who to tell --
    because the person who can fix it is not the person reading the screen.
    """
    return ("This part of the site runs on Claude, and its credit has run "
            f"out. Trying again will not help until it is topped up — please "
            f"email {SUPPORT_EMAIL} and it will be sorted out.")


def failure_notice(error, generic=GENERIC_NOTICE):
    """`credit_notice()` when the money is the problem, `generic` otherwise.

    The caller keeps its own wording for everything else: "the text could not
    be written" and "that topic could not be proposed" say which feature failed,
    which one sentence here could not.
    """
    return credit_notice() if out_of_credit(error) else generic
