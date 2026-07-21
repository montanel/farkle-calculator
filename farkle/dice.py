"""KCD2 dice, verified against the Kingdom Come: Deliverance wiki.

Source: https://kingdom-come-deliverance.fandom.com/wiki/Dice/KCD2 and the
individual die pages (Category:KCD2: Dice), fetched July 2026.

Distributions are stored as integer face weights rather than the rounded
percentages the wiki displays: every die recovers exactly to a small integer
weighting (within 0.08 percentage points), so these are the true in-game odds.

WILDCARD holds dice whose joker mechanic this solver does not model. They are
listed in the UI but cannot be selected, because scoring them as plain weighted
dice would give confidently wrong answers.
"""

from urllib.parse import quote

# name -> (weights, total, icon, description)
DICE = {
    "Aranka's die": ([6, 1, 6, 1, 6, 1], 21, "Die_f_icon.png", "Aranka gave me this die to make it easier for me to play against her husband."),
    "Balatro's die": (None, None, "Die_balatro_icon.png", "A die crafted by the balatro Jimbo, marked with his grinning face. When it lands, you get to choose how it's counted!"),
    "Cautious cheater's die": ([5, 3, 2, 3, 5, 3], 21, "Die_f_icon.png", "A die modified by an expert. It is precisely loaded, but also inconspicuous."),
    "Ci die": ([3, 3, 3, 3, 3, 8], 23, "Die_r_icon.png", "The second in the line of the demonic dice, she likes to get lost, but when she's with her sisters she's very strong."),
    "Devil's head die": ([1, 1, 1, 1, 1, 1], 6, "Die_q_devil_icon.png", "A die that feels hot to the touch. In place of a one it has a devil's head, which is not something folk like to gaze upon…"),
    "Die of misfortune": ([1, 5, 5, 5, 5, 1], 22, "Die_e_icon.png", "They say that when it rains, it pours. But if you play with this die, the only thing pouring will be your tears."),
    "Even die": ([1, 4, 1, 4, 1, 4], 15, "Die_i_icon.png", "A die loaded in favour of even numbers."),
    "Favourable die": ([6, 0, 1, 1, 6, 4], 18, "Die_j_icon.png", "A playing die that brings luck more often than you'd expect."),
    "Fer die": ([3, 3, 3, 3, 3, 8], 23, "Die_r_icon.png", "The third and last in the line of demonic dice."),
    "Greasy die": ([3, 2, 3, 2, 3, 4], 17, "Die_l_icon.png", "A more reliable die than a normal one, but it cannot be relied on for everything."),
    "Grimy die": ([1, 5, 1, 1, 7, 1], 16, "Die_c_icon.png", "One could say it will get you out of the frying pan into the fire. And sometimes it will let you stew in your own juice."),
    "Grozav's lucky die": ([1, 10, 1, 1, 1, 1], 15, "Die_m_icon.png", "It is actually not all that lucky, but don't tell anyone!"),
    "Heavenly Kingdom die": ([7, 2, 2, 2, 2, 4], 19, "Die_kcd_icon.png", "A miraculous playing die, sent from the Heavenly Kingdom to the kingdom of men."),
    "Holy Trinity die": ([4, 5, 10, 1, 1, 1], 22, "Die_g_icon.png", "A consecrated die commemorating the Holy Trinity, especially by rolling threes."),
    "Hugo's die": ([1, 1, 1, 1, 1, 1], 6, "DieSkull_icon.png", "A die of the most loyal regular at the Hole. It bears his likeness."),
    "King's die": ([4, 6, 7, 8, 4, 3], 32, "Die_q_icon.png", "An annointed head has no need of a loaded die, since he is always right and can never lose."),
    "Lousy gambler's die": ([2, 3, 2, 3, 7, 3], 20, "Die_d_icon.png", "A shoddy loaded die. It's quite noticeably unbalanced."),
    "Lu die": ([3, 3, 3, 3, 3, 8], 23, "Die_r_icon.png", "The first of the line of demonic dice."),
    "Lucky die": ([6, 1, 2, 3, 4, 6], 22, "Die_o_icon.png", "When fortune smiles on you, smile back. Otherwise you'll look suspicious."),
    "Mathematician's die": ([4, 5, 6, 7, 1, 1], 24, "Die_j_icon.png", "A die loaded based on the work of a forgotten mathematician. It may be better suited to solving equations than playing dice."),
    "Molar die": ([1, 1, 1, 1, 1, 1], 6, "DieTeeth_a_icon.png", "A die made out of a molar tooth. It's probably better not to know who it came from."),
    "Monk's die": ([8, 8, 1, 1, 1, 1], 20, "DieCursed_icon.png", "No one knows for sure whether it was blessed by a holy man or just spat on by a drunken monk. Either way, the die rolls... sort of. It's said to bring good luck to honest people. To scoundrels, it brings foolish dreams and a tendency to trip over things that weren't there before. Or maybe it's the other way around."),
    "Mother-of-pearl die": ([3, 1, 1, 1, 3, 3], 12, "ProblemovejParchant_pearlDice_icon.png", "A rare pink mother-of-pearl die. Said to bring luck in games, but misfortune in love."),
    "Odd die": ([4, 1, 4, 1, 4, 1], 15, "Die_k_icon.png", "A die loaded to favour odd numbers."),
    "Ordinary die": ([1, 1, 1, 1, 1, 1], 6, "Die_g_icon.png", "An ordinary playing die."),
    "Painted die": ([3, 1, 1, 1, 7, 3], 16, "Die_r_icon.png", "One of the dice coloured using modern techniques that hide the attempt to load it."),
    "Painter's die B": ([1, 3, 2, 2, 2, 1], 11, "Die_r_icon.png", "Painter Voyta's blue-painted die."),
    "Painter's die G": ([1, 3, 2, 2, 2, 1], 11, "Die_c_icon.png", "Painter Voyta's green-painted die."),
    "Painter's die R": ([1, 3, 2, 2, 2, 1], 11, "Die_l_icon.png", "Painter Voyta's red-painted die."),
    "Pie die": ([6, 1, 3, 3, 0, 0], 13, "Die_k_icon.png", "Doesn't look particularly tasty, but it's well balanced towards lower numbers."),
    "Premolar die": ([1, 1, 1, 1, 1, 1], 6, "DieTeeth_b_icon.png", "A playing die made from a premolar tooth."),
    "Sad Greaser's die": ([6, 6, 1, 1, 6, 3], 23, "Die_r_icon.png", "A blue die mirroring the sadness of its original owner."),
    "Saint Antiochus' die": ([3, 1, 6, 1, 1, 3], 15, "Die_q_icon.png", "The Saint Antiochus' die always rolls a 3. Not 2. And especially not 4."),
    "Shrinking die": ([2, 1, 1, 1, 1, 3], 9, "Die_p_icon.png", "A very lightly loaded die. One can barely differentiate it from an ordinary die."),
    "St. Stephen's die": ([1, 1, 1, 1, 1, 1], 6, "Die_k_icon.png", "A die blessed by St. Stephen, guaranteeing favourable numbers in the game and protection from loose stones."),
    "Strip die": ([4, 2, 2, 2, 3, 3], 16, "Die_o_icon.png", "Legend has it this die will help you undress many a wench."),
    "Tengri's die": ([2, 1, 1, 1, 1, 1], 7, "DieTeeth_a_icon.png", "This one appeared to me after I won on all of Tengri's tracks. A little token of my tremendous success, perhaps?"),
    "Trinity die": ([2, 1, 9, 1, 2, 1], 16, "Die_h_icon.png", "For some reason, this die usually rolls a three. Why?"),
    "Unbalanced die": ([3, 4, 1, 1, 2, 1], 12, "Die_b_icon.png", "A playing die someone tried to load to his advantage, but didn't do a very good job."),
    "Unlucky die": ([1, 3, 2, 2, 2, 1], 11, "Die_c_icon.png", "Sometimes Lady Luck is on your side, sometimes she isn't. With this die, she most likely isn't."),
    "Wagoner's die": ([1, 5, 6, 2, 2, 2], 18, "Die_a_icon.png", "According to legend, this die belonged to the famous Roman charioteer Arnuldus, whose tactics consisted of tiring his opponents or lulling them to sleep."),
    "Weighted die": ([10, 1, 1, 1, 1, 1], 15, "Die_e_icon.png", "A mysterious playing die found in a ruined house. Suspiciously, it tends to land on 1."),
    "Wisdom tooth die": ([1, 1, 1, 1, 1, 1], 6, "DieTeeth_c_icon.png", "A playing die made from a wisdom tooth."),
}

WILDCARD = {
    "Devil's head die": "Face 1 is a devil's head: a joker that completes any combination but never scores on its own.",
    "Balatro's die": "Every face is chosen by the player when it lands.",
}

# Wiki page titles that differ from the die name (disambiguation suffixes).
# Everything else resolves from the name directly.
_WIKI_TITLE = {
    'Ci die': 'Ci die/KCD2',
    'Die of misfortune': 'Die of misfortune/KCD2',
    'Fer die': 'Fer die/KCD2',
    'Heavenly Kingdom die': 'Heavenly Kingdom die/KCD2',
    'Holy Trinity die': 'Holy Trinity die/KCD2',
    'Lu die': 'Lu die/KCD2',
    'Lucky die': 'Lucky die/KCD2',
    'Odd die': 'Odd die/KCD2',
    'Strip die': 'Strip die/KCD2',
}

WIKI_BASE = "https://kingdom-come-deliverance.fandom.com/wiki/"

# quote() reproduces the wiki's own canonical form, apostrophes included
# (verified against action=query&prop=info&inprop=url for all 43 pages).
DICE_WIKI = {n: WIKI_BASE + quote(_WIKI_TITLE.get(n, n).replace(" ", "_"), safe="/")
             for n in DICE}

# Only dice with a modelled distribution take part in optimisation.
DICE_WEIGHTS = {n: (w, t) for n, (w, t, _, _) in DICE.items()
                if w is not None and n not in WILDCARD}

DICE_PROBS = {n: [x / t for x in w] for n, (w, t) in DICE_WEIGHTS.items()}

DICE_ICON = {n: i for n, (_, _, i, _) in DICE.items()}
DICE_DESC = {n: d for n, (_, _, _, d) in DICE.items()}
DICE_NAMES = sorted(DICE_WEIGHTS)

# The game fills any unused slot with an ordinary die, so it is always available.
DEFAULT_DIE = "Ordinary die"
