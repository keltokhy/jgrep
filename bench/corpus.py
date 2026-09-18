"""Thirty hand-labeled complaint lines and five descriptions of different grammatical shapes.

Labels are in the order of DESCRIPTIONS. The lines were written to be clear-cut, with a few
deliberate near-misses (brown tap water is not a hot-water complaint; "how much is the fine"
names no amount).
"""

DESCRIPTIONS = [
    "a complaint about noise",              # noun phrase
    "the writer asks a question",           # statement
    "mentions a specific dollar amount",    # verb phrase, number-adjacent
    "does not mention a landlord",          # negation, a documented weak spot
    "is this about heat or hot water?",     # question form
]

LINES = [
    ("The bar downstairs blasts music until 4am every single night.", 1, 0, 0, 1, 0),
    ("No heat in the apartment for three days and it is 20 degrees outside.", 0, 0, 0, 1, 1),
    ("My landlord raised the rent by $400 with no notice.", 0, 0, 1, 0, 0),
    ("Can someone tell me when the hot water will be back?", 0, 1, 0, 1, 1),
    ("Construction next door starts jackhammering at 6am, is that even legal?", 1, 1, 0, 1, 0),
    ("The landlord still has not fixed the boiler and we have no hot water.", 0, 0, 0, 0, 1),
    ("I was charged a $75 late fee even though I paid on time.", 0, 0, 1, 1, 0),
    ("There is a pothole on Atlantic Avenue near 4th that has been there for months.", 0, 0, 0, 1, 0),
    ("Why does my landlord get to ignore the broken radiator all winter?", 0, 1, 0, 0, 1),
    ("The upstairs neighbor's dog barks all day while they are at work.", 1, 0, 0, 1, 0),
    ("Thank you to the sanitation crew for the quick pickup on our block.", 0, 0, 0, 1, 0),
    ("Street light out at the corner of Dekalb and Adelphi.", 0, 0, 0, 1, 0),
    ("The super said the repair would cost $1,200 and the landlord refuses to pay.", 0, 0, 1, 0, 0),
    ("Car alarms going off for hours on my street, I cannot sleep.", 1, 0, 0, 1, 0),
    ("Is there a number I can call about rats in the building?", 0, 1, 0, 1, 0),
    ("Our radiators are stone cold and the kids are sleeping in coats.", 0, 0, 0, 1, 1),
    ("The parking ticket was $115 for a sign that was covered by scaffolding.", 0, 0, 1, 1, 0),
    ("Helicopters fly over the park every ten minutes and the noise is unbearable.", 1, 0, 0, 1, 0),
    ("How much is the fine for a blocked fire hydrant?", 0, 1, 0, 1, 0),
    ("The landlord's contractor drills through the wall at night and it is deafening.", 1, 0, 0, 0, 0),
    ("Bus stop shelter glass is shattered on Flatbush Ave.", 0, 0, 0, 1, 0),
    ("Water comes out brown from the kitchen tap.", 0, 0, 0, 1, 0),
    ("They want $3,000 for a security deposit, is that allowed?", 0, 1, 1, 1, 0),
    ("Ice cream truck jingle plays outside my window for an hour every evening.", 1, 0, 0, 1, 0),
    ("The elevator has been out of service since Monday.", 0, 0, 0, 1, 0),
    ("When will the landlord turn the heat on? It is November.", 0, 1, 0, 0, 1),
    ("Garbage has not been collected on our street in over a week.", 0, 0, 0, 1, 0),
    ("A tree branch fell on my car during the storm.", 0, 0, 0, 1, 0),
    ("The shower only runs cold, no hot water since the weekend.", 0, 0, 0, 1, 1),
    ("Rent is going up to $2,850 next month according to the letter from my landlord.", 0, 0, 1, 0, 0),
]
