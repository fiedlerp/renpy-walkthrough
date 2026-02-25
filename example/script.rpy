# example/script.rpy
#
# A self-contained Ren'Py script demonstrating the flow structures that
# parse_rpy.py recognises and turns into graph edges:
#
#   * label      → graph node
#   * jump       → "jump" edge
#   * call/return→ "call" edge (return is implicit)
#   * menu       → "menu" edge per choice
#   * if/elif    → "jump" / "call" edges inside each branch
#
# Run the parser against this file's parent directory:
#
#   python parse_rpy.py example/ --renpy-sdk /path/to/renpy-sdk
#
# Expected graph (simplified):
#
#   start ──jump──► chapter_one
#   chapter_one ──menu──► good_path   (choice "Take the left path")
#   chapter_one ──menu──► bad_path    (choice "Take the right path")
#   chapter_one ──call──► flashback
#   good_path ──jump──► good_ending
#   bad_path  ──jump──► bad_ending
#   good_ending ──jump──► epilogue
#   bad_ending  ──jump──► epilogue

define e = Character("Eileen")

label start:
    e "Welcome to the example game."
    e "Our story begins now…"
    jump chapter_one

label chapter_one:
    e "You stand at a crossroads."
    call flashback
    menu:
        "Take the left path":
            jump good_path
        "Take the right path":
            jump bad_path

label flashback:
    e "You remember a happier time…"
    return

label good_path:
    e "You chose wisely."
    if True:
        e "The sun shines brightly."
    jump good_ending

label bad_path:
    e "You chose poorly."
    jump bad_ending

label good_ending:
    e "And they lived happily ever after."
    jump epilogue

label bad_ending:
    e "Darkness fell upon the land."
    jump epilogue

label epilogue:
    e "Whatever the path, the journey shaped you."
    return
