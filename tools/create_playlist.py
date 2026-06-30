import os
from pathlib import Path

playlist = {
    "1_amazing_grace.txt": [
        "Amazing grace how sweet the sound",
        "That saved a wretch like me",
        "I once was lost but now am found",
        "Was blind but now I see"
    ],
    "2_how_great_thou_art.txt": [
        "O Lord my God when I in awesome wonder",
        "Consider all the worlds Thy hands have made",
        "I see the stars I hear the rolling thunder",
        "Thy power throughout the universe displayed",
        "Then sings my soul my Savior God to Thee",
        "How great Thou art how great Thou art"
    ],
    "3_holy_holy_holy.txt": [
        "Holy holy holy Lord God Almighty",
        "Early in the morning our song shall rise to Thee",
        "Holy holy holy merciful and mighty",
        "God in three persons blessed Trinity"
    ],
    "4_be_thou_my_vision.txt": [
        "Be Thou my vision O Lord of my heart",
        "Naught be all else to me save that Thou art",
        "Thou my best thought by day or by night",
        "Waking or sleeping Thy presence my light"
    ],
    "5_come_thou_fount.txt": [
        "Come Thou fount of every blessing",
        "Tune my heart to sing Thy grace",
        "Streams of mercy never ceasing",
        "Call for songs of loudest praise"
    ],
    "6_it_is_well.txt": [
        "When peace like a river attendeth my way",
        "When sorrows like sea billows roll",
        "Whatever my lot Thou hast taught me to say",
        "It is well it is well with my soul"
    ],
    "7_in_christ_alone.txt": [
        "In Christ alone my hope is found",
        "He is my light my strength my song",
        "This cornerstone this solid ground",
        "Firm through the fiercest drought and storm"
    ],
    "8_ten_thousand_reasons.txt": [
        "Bless the Lord O my soul O my soul",
        "Worship His holy name",
        "Sing like never before O my soul",
        "I'll worship Your holy name"
    ],
    "9_here_i_am_to_worship.txt": [
        "Here I am to worship here I am to bow down",
        "Here I am to say that You're my God",
        "You're altogether lovely altogether worthy",
        "Altogether wonderful to me"
    ],
    "10_great_are_you_lord.txt": [
        "You give life You are love",
        "You bring light to the darkness",
        "You give hope You restore",
        "Every heart that is broken",
        "Great are You Lord"
    ]
}

os.makedirs("tests/playlist", exist_ok=True)
for filename, lines in playlist.items():
    with open(f"tests/playlist/{filename}", "w") as f:
        f.write("\n\n".join(lines))
    print(f"Created {filename}")
