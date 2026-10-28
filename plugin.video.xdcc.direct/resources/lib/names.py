"""Nomi per il nick IRC: uno a caso alla prima apertura, poi l'utente lo può cambiare. Nomi normali, niente numeri
(i nick con tante cifre sembrano bot)."""

import random

NAMES = (
    "alberto", "alessandro", "alessia", "alice", "andrea", "angela", "anna", "antonio", "arianna", "aurora",
    "barbara", "beatrice", "bruno", "camilla", "carla", "carlo", "chiara", "claudio", "cristina", "daniela",
    "daniele", "dario", "davide", "debora", "diego", "edoardo", "elena", "eleonora", "elisa", "emanuele",
    "emma", "enrico", "ettore", "fabio", "federica", "federico", "filippo", "francesca", "franco", "gabriele",
    "giacomo", "ginevra", "giorgia", "giorgio", "giovanni", "giulia", "giuseppe", "greta", "guido", "ilaria",
    "irene", "ivan", "laura", "leonardo", "lorena", "lorenzo", "luca", "lucia", "manuela", "marco",
    "maria", "marta", "martina", "massimo", "matteo", "mattia", "mauro", "michele", "mirko", "monica",
    "nadia", "nicola", "noemi", "paola", "paolo", "patrizia", "pietro", "renato", "riccardo", "rita",
    "roberta", "roberto", "rosa", "salvatore", "samuele", "sara", "serena", "sergio", "silvia", "simona",
    "simone", "sofia", "sonia", "stefano", "teresa", "tiziana", "tommaso", "valentina", "veronica", "viola",
)


def random_name():
    return random.choice(NAMES)
