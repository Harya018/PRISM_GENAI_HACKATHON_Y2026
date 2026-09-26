"""40 self-written disfluent sentences for agent/resolver.py, per the task's integrity rule:
tune only on our own examples, never on FDB-v3 benchmark items. Domains echo the benchmark's
general shape (travel/finance/housing/shopping) because that's the natural shape of this kind of
assistant task, but every sentence, name, and number below is invented for this test suite, not
copied or paraphrased from any FDB-v3 scenario.

Each case: (raw_text, tool_schema, expected_slots, note).
"""

FLIGHT_SCHEMA = {"args": {"destination": {"type": "string"}, "date": {"type": "string"}}}
CARD_SCHEMA = {"args": {"card_type": {"type": "string", "enum": ["platinum", "gold", "silver"]}}}
APT_SCHEMA = {"args": {"city": {"type": "string"},
                      "bedrooms": {"type": "number", "description": "Number of bedrooms"},
                      "max_price": {"type": "number", "description": "Maximum monthly rent budget"}}}
PRODUCT_SCHEMA = {"args": {"query": {"type": "string"}, "quantity": {"type": "number"}}}
NAME_SCHEMA = {"args": {"passenger_name": {"type": "string"}}}

CASES = [
    # --- plain fillers, no correction ---
    ("Um, I need a flight to Denver on August tenth.", FLIGHT_SCHEMA,
     {"destination": "Denver", "date": "August 10"}, "filler only"),
    ("So, uh, can you find me flights to Chicago for July 3rd?", FLIGHT_SCHEMA,
     {"destination": "Chicago", "date": "July 3"}, "filler only"),
    ("I guess I want to go to Miami, sort of around June 1st.", FLIGHT_SCHEMA,
     {"destination": "Miami", "date": "June 1"}, "filler only"),
    ("You know, book me a flight to Boston on May 5th please.", FLIGHT_SCHEMA,
     {"destination": "Boston", "date": "May 5"}, "filler only"),

    # --- false starts (stutter) ---
    ("I need the the apartment in Austin with two bedrooms.", APT_SCHEMA,
     {"city": "Austin", "bedrooms": "2"}, "stutter"),
    ("Can you can you check card benefits for my gold card?", CARD_SCHEMA,
     {"card_type": "gold"}, "stutter"),

    # --- false start (dash truncation) ---
    ("I want to— actually, find me an apartment in Denver, three bedrooms.", APT_SCHEMA,
     {"city": "Denver", "bedrooms": "3"}, "dash truncation + correction"),
    ("Book a— I mean, search flights to Seattle for September 9th.", FLIGHT_SCHEMA,
     {"destination": "Seattle", "date": "September 9"}, "dash truncation + correction"),

    # --- simple self-correction, segment split, city ---
    ("Find flights to Paris — actually, no, Berlin instead.", FLIGHT_SCHEMA,
     {"destination": "Berlin"}, "correction: city"),
    ("Search apartments in Austin, wait, I mean Dallas.", APT_SCHEMA,
     {"city": "Dallas"}, "correction: city"),
    ("Get me exchange rates involving Japan, sorry, Korea.", FLIGHT_SCHEMA,
     {"destination": "Korea"}, "correction: proper noun generic"),
    ("I'd like a flight to Rome, no wait, Madrid, on April 2nd.", FLIGHT_SCHEMA,
     {"destination": "Madrid", "date": "April 2"}, "correction: city, date unaffected"),

    # --- self-correction, "not X but Y" ---
    ("Not Tokyo but Osaka, please look up flights.", FLIGHT_SCHEMA,
     {"destination": "Osaka"}, "not X but Y"),
    ("I want not three bedrooms but two bedrooms in Miami.", APT_SCHEMA,
     {"city": "Miami", "bedrooms": "2"}, "not X but Y: number"),
    ("Give me benefits for not the silver card but the platinum card.", CARD_SCHEMA,
     {"card_type": "platinum"}, "not X but Y: enum"),

    # --- correction of a number ---
    ("Add two, actually four, of those headphones to my cart.", PRODUCT_SCHEMA,
     {"quantity": "4"}, "correction: number"),
    ("I need three, sorry, five bedrooms in Chicago.", APT_SCHEMA,
     {"city": "Chicago", "bedrooms": "5"}, "correction: number"),
    ("Book two tickets — make that three — to Denver.", FLIGHT_SCHEMA,
     {"destination": "Denver"}, "correction: number kept out of destination schema"),

    # --- correction of a date ---
    ("Search flights to Vegas for June 5th, actually June 12th.", FLIGHT_SCHEMA,
     {"destination": "Vegas", "date": "June 12"}, "correction: date"),
    ("I'll travel on March 1st — no, March 3rd — to Orlando.", FLIGHT_SCHEMA,
     {"destination": "Orlando", "date": "March 3"}, "correction: date"),

    # --- correction of enum ---
    ("Tell me the benefits of the gold card, wait, the platinum card.", CARD_SCHEMA,
     {"card_type": "platinum"}, "correction: enum"),
    ("Check my silver card — actually the gold one — for benefits.", CARD_SCHEMA,
     {"card_type": "gold"}, "correction: enum"),

    # --- multiple corrections in one turn ---
    ("Flights to Paris, no Berlin, for May 1st, actually May 8th.", FLIGHT_SCHEMA,
     {"destination": "Berlin", "date": "May 8"}, "double correction"),
    ("Two bedrooms in Austin, wait, Dallas, actually make it three bedrooms.", APT_SCHEMA,
     {"city": "Dallas", "bedrooms": "3"}, "double correction"),

    # --- hesitation without correction ---
    ("Um... I think... I'd like flights to Denver for July 4th.", FLIGHT_SCHEMA,
     {"destination": "Denver", "date": "July 4"}, "hesitation only"),
    ("Let's see, um, how about an apartment in Seattle, two bedrooms.", APT_SCHEMA,
     {"city": "Seattle", "bedrooms": "2"}, "hesitation only"),

    # --- retraction with no replacement value stated for that slot ---
    ("Never mind the flight, actually get me an apartment in Boston.", APT_SCHEMA,
     {"city": "Boston"}, "retraction, different domain"),

    # --- passenger name correction ---
    ("Book it for Alice, no wait, Alicia.", NAME_SCHEMA,
     {"passenger_name": "Alicia"}, "correction: name"),
    ("The passenger is Robert — sorry, Roberto.", NAME_SCHEMA,
     {"passenger_name": "Roberto"}, "correction: name"),

    # --- no disfluency at all (control cases) ---
    ("Search flights to Denver for August 20th.", FLIGHT_SCHEMA,
     {"destination": "Denver", "date": "August 20"}, "clean control"),
    ("Find an apartment in Austin with two bedrooms under $2000.", APT_SCHEMA,
     {"city": "Austin", "bedrooms": "2", "max_price": "2000"}, "clean control"),
    ("Add three headphones to my cart.", PRODUCT_SCHEMA,
     {"quantity": "3"}, "clean control"),
    ("What are the benefits of my platinum card?", CARD_SCHEMA,
     {"card_type": "platinum"}, "clean control"),

    # --- filler + correction combined ---
    ("Um, so, flights to, uh, Chicago — no wait, Detroit — for June 1st.", FLIGHT_SCHEMA,
     {"destination": "Detroit", "date": "June 1"}, "filler + correction"),
    ("Uh, apartment in, um, Dallas, actually Fort Worth, three bedrooms.", APT_SCHEMA,
     {"city": "Fort Worth", "bedrooms": "3"}, "filler + correction"),

    # --- correction word appearing as part of a normal phrase (must not misfire) ---
    ("I actually really like flights to Denver for July 4th.", FLIGHT_SCHEMA,
     {"destination": "Denver", "date": "July 4"}, "'actually' as emphasis, not correction"),
    ("Sorry to bother you, I'd like flights to Miami on May 1st.", FLIGHT_SCHEMA,
     {"destination": "Miami", "date": "May 1"}, "'sorry' as politeness, not correction"),

    # --- longer, more natural disfluent utterance ---
    ("Hey, um, so I was thinking, could you, uh, search flights to — "
     "actually hold on, I meant Vancouver, not Victoria — for September 15th?", FLIGHT_SCHEMA,
     {"destination": "Vancouver", "date": "September 15"}, "long natural disfluency"),
    ("Okay so I need, um, two bedrooms — no sorry, make it four bedrooms — "
     "in Portland under $3000.", APT_SCHEMA,
     {"city": "Portland", "bedrooms": "4", "max_price": "3000"}, "long natural disfluency"),

    # --- correction late in a longer sentence ---
    ("Please look up flights heading to Denver for the trip departing on August 9th, "
     "actually make that August 11th.", FLIGHT_SCHEMA,
     {"destination": "Denver", "date": "August 11"}, "late correction"),
    ("I would like to book two of the wireless keyboards, on second thought, "
     "make that five of them.", PRODUCT_SCHEMA,
     {"quantity": "5"}, "late correction"),

    # --- multiple fillers stacked ---
    ("Um, uh, so, like, you know, I need a flight to Reno for October 1st.", FLIGHT_SCHEMA,
     {"destination": "Reno", "date": "October 1"}, "stacked fillers"),

    # --- compound spelled-out numbers (magnitude words: hundred/thousand) — added after a real
    # FDB-v3 rollback-subset run exposed that the old single-word-only number parser read "two
    # thousand" as just "two" (2), silently corrupting a numeric arg the model had already gotten
    # right; the fix (agent/resolver.py's `_words_to_number`) is general English number-word
    # grammar, not tuned to this specific sentence ---
    ("Find an apartment in Miami, two bedrooms, max price around two thousand dollars a month.",
     APT_SCHEMA, {"city": "Miami", "bedrooms": "2", "max_price": "2000"},
     "compound number: two thousand"),
    ("I'm looking in Denver for a place around twenty five hundred a month, three bedrooms.",
     APT_SCHEMA, {"city": "Denver", "bedrooms": "3", "max_price": "2500"},
     "compound number: tens + hundred"),
    ("Budget for the apartment in Tampa is one hundred fifty over what I said, two bedrooms.",
     APT_SCHEMA, {"city": "Tampa", "bedrooms": "2", "max_price": "150"},
     "compound number: hundred + tens, no thousand"),

    # --- self-correction combined with a compound spelled-out number in the corrected clause:
    # the exact shape (a city correction via "instead" followed by a large spelled-out price in
    # the same turn) that surfaced the bug above ---
    ("I'm interested in a two-bedroom in Seattle — wait, actually, let's look in Denver instead, "
     "and keep the max price around three thousand dollars a month.", APT_SCHEMA,
     {"city": "Denver", "bedrooms": "2", "max_price": "3000"},
     "correction + compound number + 'instead' mid-clause"),
]

assert len(CASES) >= 40, f"only {len(CASES)} cases — need at least 40"
