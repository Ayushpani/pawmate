# Put your cat sprite pack in this folder

Unzip a downloaded sprite pack directly into this folder. Nested folders are
fine — discovery is recursive, so all of these work:

    assets/cat/walk/0.png, 1.png, ...
    assets/cat/Black Cat Sprites/PNG/walk/frame_00.png, ...
    assets/cat/walk_01.png, walk_02.png, ...
    assets/cat/walk.png              (a horizontal strip — auto-sliced)
    assets/cat/somepack.zip          (auto-extracted on next run)

Then check what was found:

    python pawmate_prototype.py --inspect

That lists every animation group it discovered (including ones whose names
it didn't recognise) and writes `assets/contact_sheet.png` so you can see
every loaded frame.

Free packs that work well:
  - https://carysaurus.itch.io/black-cat-sprites
  - https://frolicforge.itch.io/cat-animation-high-res

Note: sprite packs usually have their own license terms. Check whether the
pack allows redistribution before committing its files to this repo.
