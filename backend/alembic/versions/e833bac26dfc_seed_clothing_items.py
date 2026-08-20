"""seed clothing items

Revision ID: e833bac26dfc
Revises: 7472ed35683a
Create Date: 2026-08-20 20:56:45.995932

DATA-ONLY migration: clothing_items already exists (initial schema), so
this revision changes no DDL — it only seeds the catalog rows used to
exercise browsing, filtering and pagination. item_id and created_at are
left to their server defaults (gen_random_uuid() / now()), and
availability_status uses the native clothing_availability enum values
("AVAILABLE" / "UNAVAILABLE" / "UPCOMING") exactly as the initial
migration seeded the category lookup rows. collection_id stays NULL for
all seeds (the clothing_collections table is a future extension point).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e833bac26dfc'
down_revision: Union[str, Sequence[str], None] = '7472ed35683a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Seed the clothing shop catalog."""
    # Resolve the seeded categories by NAME (not by a hard-coded id), so
    # the seed survives any change to the autoincrement sequence.
    bind = op.get_bind()
    category_id = {
        name: bind.execute(
            sa.text(
                "SELECT category_id FROM clothing_categories "
                "WHERE category_name = :n"
            ),
            {"n": name},
        ).scalar_one()
        for name in (
            "Hairstyles",
            "Hats",
            "Tops",
            "Bottoms",
            "Sneakers",
            "Sunglasses",
        )
    }

    # (name, description, category_name, price, image_url, availability)
    items = [
        # --- Hairstyles -------------------------------------------------
        ("Windblown Waves", None, "Hairstyles", 300,
         "https://mycolabear.example.com/clothing/hairstyles/windblown-waves.png",
         "AVAILABLE"),
        ("Classic Crew Cut", "A timeless, low-maintenance trim.",
         "Hairstyles", 250,
         "https://mycolabear.example.com/clothing/hairstyles/classic-crew-cut.png",
         "AVAILABLE"),
        ("Neon Faux Hawk", "Summer drop — the most electric hair in town.",
         "Hairstyles", 400,
         "https://mycolabear.example.com/clothing/hairstyles/neon-faux-hawk.png",
         "UPCOMING"),
        # --- Hats --------------------------------------------------------
        ("Polar Snapback", "Flat brim, adjustable strap, ice-cold fit.",
         "Hats", 350,
         "https://mycolabear.example.com/clothing/hats/polar-snapback.png",
         "AVAILABLE"),
        ("Sunrise Bucket Hat", "Beach-ready and fully reversible.",
         "Hats", 200,
         "https://mycolabear.example.com/clothing/hats/sunrise-bucket-hat.png",
         "AVAILABLE"),
        ("Winter Toque Deluxe", "Sold out for the season — cozy fleece lining.",
         "Hats", 280,
         "https://mycolabear.example.com/clothing/hats/winter-toque-deluxe.png",
         "UNAVAILABLE"),
        # --- Tops --------------------------------------------------------
        ("Cola Classic Tee", "The everyday classic, 100% cotton.",
         "Tops", 150,
         "https://mycolabear.example.com/clothing/tops/cola-classic-tee.png",
         "AVAILABLE"),
        ("Polar Hoodie", "Heavyweight fleece with a hidden pocket.",
         "Tops", 500,
         "https://mycolabear.example.com/clothing/tops/polar-hoodie.png",
         "AVAILABLE"),
        ("Limited Cold Brew Jacket", "Releasing soon — the collectors' piece.",
         "Tops", 900,
         "https://mycolabear.example.com/clothing/tops/limited-cold-brew-jacket.png",
         "UPCOMING"),
        # --- Bottoms -----------------------------------------------------
        ("Soda Pop Shorts", "Lightweight summer shorts, two zip pockets.",
         "Bottoms", 180,
         "https://mycolabear.example.com/clothing/bottoms/soda-pop-shorts.png",
         "AVAILABLE"),
        ("Chill Cargo Pants", "Six pockets, tapered fit, zero compromises.",
         "Bottoms", 420,
         "https://mycolabear.example.com/clothing/bottoms/chill-cargo-pants.png",
         "AVAILABLE"),
        ("Retro Racer Tracksuit Pants", "Limited run from last season.",
         "Bottoms", 380,
         "https://mycolabear.example.com/clothing/bottoms/retro-racer-tracksuit-pants.png",
         "UNAVAILABLE"),
        # --- Sneakers ----------------------------------------------------
        ("Fizzy Kicks", "Bubble-soled sneakers with a carbon pop of colour.",
         "Sneakers", 600,
         "https://mycolabear.example.com/clothing/sneakers/fizzy-kicks.png",
         "AVAILABLE"),
        ("Bubbles Running Shoes", "Cushioned everyday runners.",
         "Sneakers", 550,
         "https://mycolabear.example.com/clothing/sneakers/bubbles-running-shoes.png",
         "AVAILABLE"),
        ("Midnight Cola High-Tops", "Dropping soon — midnight gloss upper.",
         "Sneakers", 750,
         "https://mycolabear.example.com/clothing/sneakers/midnight-cola-high-tops.png",
         "UPCOMING"),
        # --- Sunglasses --------------------------------------------------
        ("Polar Shades", "Classic aviator cut with UV400 protection.",
         "Sunglasses", 120,
         "https://mycolabear.example.com/clothing/sunglasses/polar-shades.png",
         "AVAILABLE"),
        ("Ice Cool Sunglasses", "Frost-tinted lenses for bright days.",
         "Sunglasses", 140,
         "https://mycolabear.example.com/clothing/sunglasses/ice-cool-sunglasses.png",
         "AVAILABLE"),
        ("Retro Cola Aviators", "Iconic gold frame from the archive vault.",
         "Sunglasses", 220,
         "https://mycolabear.example.com/clothing/sunglasses/retro-cola-aviators.png",
         "UNAVAILABLE"),
    ]

    op.bulk_insert(
        sa.table(
            "clothing_items",
            sa.column("name", sa.String()),
            sa.column("description", sa.String()),
            sa.column("category_id", sa.SmallInteger()),
            sa.column("price", sa.Integer()),
            sa.column("image_url", sa.String()),
            sa.column(
                "availability_status",
                postgresql.ENUM(name="clothing_availability", create_type=False),
            ),
        ),
        [
            {
                "name": name,
                "description": description,
                "category_id": category_id[category_name],
                "price": price,
                "image_url": image_url,
                "availability_status": availability,
            }
            for (name, description, category_name, price, image_url, availability) in items
        ],
    )


def downgrade() -> None:
    """Remove the catalog rows this migration seeded."""
    bind = op.get_bind()
    names = [
        "Windblown Waves",
        "Classic Crew Cut",
        "Neon Faux Hawk",
        "Polar Snapback",
        "Sunrise Bucket Hat",
        "Winter Toque Deluxe",
        "Cola Classic Tee",
        "Polar Hoodie",
        "Limited Cold Brew Jacket",
        "Soda Pop Shorts",
        "Chill Cargo Pants",
        "Retro Racer Tracksuit Pants",
        "Fizzy Kicks",
        "Bubbles Running Shoes",
        "Midnight Cola High-Tops",
        "Polar Shades",
        "Ice Cool Sunglasses",
        "Retro Cola Aviators",
    ]
    bind.execute(
        sa.text(
            "DELETE FROM clothing_items WHERE name IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": tuple(names)},
    )
