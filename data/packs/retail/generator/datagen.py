# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Lumen Retail Group tables: stores, products, customers, orders, order_items, returns and the two
merchandising-plan sheets.

Everything is synthetic and comes from seeded numpy streams, so the same seed gives the same bytes. The tables are
a sample of the loyalty program's transactions (not every till receipt), which keeps the dataset small enough for
a demo while leaving enough history (April 2025 to September 2026) for NVIDIA Kumo to learn from.
"""

from __future__ import annotations

from datetime import date
from datetime import datetime
from datetime import timedelta

import numpy as np
import pandas as pd

SEED = 1148
WINDOW_START = date(2025, 4, 1)
WINDOW_END = date(2026, 9, 30)
AS_OF = WINDOW_END
N_CUSTOMERS = 1500
RETURN_SCALE = 0.85
ORDER_RATE_SCALE = 1.02  # calibrates the order count (about 5,600) to the row budget

REGIONS = ["Northeast", "Southeast", "Midwest", "West"]
CATEGORIES = [
    "Electronics",
    "Furniture",
    "Home Decor",
    "Kitchen & Dining",
    "Bedding & Bath",
    "Apparel",
    "Outdoor & Garden",
]

# Stores that carry the planted stories (see content/loss-prevention-memo.md and the Q3 review).
LP_MONITORED_STORES = ["S06", "S16", "S22"]  # enhanced return monitoring (return rate above 1.5x the chain rate)
ELECTRONICS_SPIKE_STORE = "S13"  # Chicago Loop: electronics returns spike for Q3 2026 purchases
ELECTRONICS_SPIKE_START = date(2026, 6, 24)

# store_id, name, type, region, state, city, opened_on, square_feet, sales weight
STORES = [
    ("S01", "Lumen Boston Seaport", "flagship", "Northeast", "MA", "Boston", date(2014, 3, 14), 41000, 2.2),
    ("S02", "Lumen Providence Place", "mall", "Northeast", "RI", "Providence", date(2016, 9, 22), 22000, 1.0),
    ("S03", "Lumen Hartford Crossing", "standalone", "Northeast", "CT", "Hartford", date(2012, 5, 5), 28000, 1.1),
    ("S04", "Lumen Albany Commons", "outlet", "Northeast", "NY", "Albany", date(2018, 10, 19), 19000, 0.8),
    ("S05", "Lumen Philadelphia Center City", "mall", "Northeast", "PA", "Philadelphia", date(2011, 4, 2), 26000, 1.2),
    ("S06", "Lumen Newark Gateway", "standalone", "Northeast", "NJ", "Newark", date(2019, 8, 30), 27000, 1.1),
    ("S07", "Lumen Atlanta Midtown", "flagship", "Southeast", "GA", "Atlanta", date(2015, 11, 6), 39000, 3.0),
    ("S08", "Lumen Charlotte South End", "mall", "Southeast", "NC", "Charlotte", date(2017, 3, 11), 23000, 1.0),
    ("S09", "Lumen Raleigh Triangle", "standalone", "Southeast", "NC", "Raleigh", date(2013, 6, 8), 27000, 1.0),
    ("S10", "Lumen Nashville Music Row", "mall", "Southeast", "TN", "Nashville", date(2020, 2, 21), 21000, 1.0),
    ("S11", "Lumen Tampa Bayshore", "outlet", "Southeast", "FL", "Tampa", date(2018, 1, 26), 20000, 0.8),
    ("S12", "Lumen Orlando Premium", "outlet", "Southeast", "FL", "Orlando", date(2016, 12, 3), 21000, 1.0),
    ("S13", "Lumen Chicago Loop", "flagship", "Midwest", "IL", "Chicago", date(2013, 10, 12), 44000, 2.6),
    ("S14", "Lumen Minneapolis North Loop", "mall", "Midwest", "MN", "Minneapolis", date(2017, 8, 25), 22000, 1.0),
    ("S15", "Lumen Columbus Easton", "standalone", "Midwest", "OH", "Columbus", date(2014, 9, 13), 26000, 1.0),
    ("S16", "Lumen Indianapolis Fashion Mall", "mall", "Midwest", "IN", "Indianapolis", date(2015, 4, 18), 22000, 0.9),
    ("S17", "Lumen Kansas City Plaza", "standalone", "Midwest", "MO", "Kansas City", date(2019, 5, 4), 25000, 0.9),
    ("S18", "Lumen Milwaukee Third Ward", "outlet", "Midwest", "WI", "Milwaukee", date(2021, 3, 27), 18000, 0.7),
    ("S19", "Lumen Seattle South Lake Union", "mall", "West", "WA", "Seattle", date(2016, 6, 11), 23000, 1.2),
    ("S20", "Lumen Portland Pearl", "standalone", "West", "OR", "Portland", date(2014, 11, 1), 24000, 1.0),
    (
        "S21",
        "Lumen San Francisco Union Square",
        "flagship",
        "West",
        "CA",
        "San Francisco",
        date(2012, 9, 7),
        38000,
        3.7,
    ),
    ("S22", "Lumen Los Angeles Westside", "mall", "West", "CA", "Los Angeles", date(2015, 2, 14), 30000, 1.6),
    ("S23", "Lumen Phoenix Biltmore", "standalone", "West", "AZ", "Phoenix", date(2018, 7, 14), 26000, 1.0),
    ("S24", "Lumen Denver Cherry Creek", "mall", "West", "CO", "Denver", date(2025, 8, 15), 21000, 1.0),
]
WEB_STORE = ("WEB", "Lumen.com", "ecommerce", "E-commerce", None, None, date(2010, 1, 1), None, 0.0)

SUPPLIERS = {
    "Auralis Audio Works": "Auralis",
    "Voltara Devices": "Voltara",
    "Hearthwood Furniture Co.": "Hearthwood",
    "Cedarline Home Furnishings": "Cedarline",
    "Brightmoor Decor": "Brightmoor",
    "Copperkettle Kitchenware": "Copperkettle",
    "Softfold Textiles": "Softfold",
    "Tamarack Apparel Group": "Tamarack",
    "Wildfern Outdoor Supply": "Wildfern",
    "Meridian Home Goods": "Meridian",
}

# category -> [(subcategory, supplier, low price, high price, variants)]
CATALOG = {
    "Electronics": [
        ("Smart Speaker", "Voltara Devices", 49, 129, ["Mini", "Standard", "Max"]),
        ("Soundbar", "Auralis Audio Works", 129, 349, ["2.1", "3.1 with Subwoofer"]),
        ("Wireless Earbuds", "Auralis Audio Works", 39, 149, ["Lite", "Pro"]),
        ("Over-Ear Headphones", "Auralis Audio Works", 79, 299, ["Studio", "Travel"]),
        ("Smart Display", "Voltara Devices", 89, 229, ["8-inch", "10-inch"]),
        ("4K Streaming Stick", "Voltara Devices", 29, 59, ["Standard"]),
        ("Portable Projector", "Voltara Devices", 149, 449, ["HD", "Full HD"]),
        ("Robot Vacuum", "Voltara Devices", 199, 599, ["Core", "Plus", "Max"]),
        ("Air Purifier", "Voltara Devices", 89, 299, ["Small Room", "Large Room"]),
        ("Smart Thermostat", "Voltara Devices", 79, 179, ["Standard", "Pro"]),
        ("Video Doorbell", "Voltara Devices", 69, 199, ["Wired", "Battery"]),
        ("Bluetooth Speaker", "Auralis Audio Works", 29, 119, ["Pocket", "Party"]),
        ("Smart LED Light Kit", "Voltara Devices", 24, 89, ["Starter", "Whole Room"]),
        ("Wireless Charging Pad", "Voltara Devices", 19, 49, ["Single", "Triple"]),
        ("Digital Photo Frame", "Voltara Devices", 59, 139, ["10-inch", "15-inch"]),
        ("Sleep Sound Machine", "Auralis Audio Works", 29, 79, ["Classic", "Smart"]),
    ],
    "Furniture": [
        ("3-Seat Sofa", "Hearthwood Furniture Co.", 699, 1299, ["Slate", "Linen", "Oat"]),
        ("Armchair", "Cedarline Home Furnishings", 329, 599, ["Slate", "Rust"]),
        ("Coffee Table", "Hearthwood Furniture Co.", 169, 399, ["Oak", "Walnut"]),
        ("Dining Table", "Cedarline Home Furnishings", 449, 899, ["6-Seat", "8-Seat"]),
        ("Dining Chair Set", "Cedarline Home Furnishings", 189, 399, ["Set of 2", "Set of 4"]),
        ("Bookshelf", "Hearthwood Furniture Co.", 129, 349, ["4-Shelf", "5-Shelf"]),
        ("TV Stand", "Hearthwood Furniture Co.", 149, 449, ["55-inch", "65-inch"]),
        ("Queen Bed Frame", "Cedarline Home Furnishings", 349, 799, ["Upholstered", "Platform"]),
        ("Nightstand", "Cedarline Home Furnishings", 89, 229, ["1-Drawer", "2-Drawer"]),
        ("Dresser", "Hearthwood Furniture Co.", 299, 649, ["6-Drawer", "8-Drawer"]),
        ("Writing Desk", "Cedarline Home Furnishings", 179, 449, ["Compact", "Standard"]),
        ("Office Chair", "Cedarline Home Furnishings", 149, 379, ["Mesh", "Leather"]),
        ("Accent Bench", "Hearthwood Furniture Co.", 119, 249, ["Entryway"]),
        ("Console Table", "Hearthwood Furniture Co.", 139, 329, ["Narrow", "Wide"]),
    ],
    "Home Decor": [
        ("Throw Pillow Set", "Brightmoor Decor", 24, 59, ["Sage", "Terracotta", "Ink"]),
        ("Framed Wall Art", "Brightmoor Decor", 39, 149, ["Botanical", "Abstract", "Coastal"]),
        ("Table Lamp", "Brightmoor Decor", 39, 119, ["Ceramic", "Brass"]),
        ("Floor Lamp", "Meridian Home Goods", 69, 189, ["Arc", "Tripod"]),
        ("Area Rug", "Meridian Home Goods", 89, 349, ["5x8 Geometric", "8x10 Woven"]),
        ("Vase Set", "Brightmoor Decor", 19, 69, ["Glass", "Stoneware"]),
        ("Wall Mirror", "Brightmoor Decor", 49, 199, ["Round", "Arched"]),
        ("Scented Candle Set", "Meridian Home Goods", 14, 49, ["Linen", "Cedar"]),
        ("Picture Frame Set", "Brightmoor Decor", 19, 59, ["Gallery 7-Piece"]),
        ("Wall Clock", "Meridian Home Goods", 29, 89, ["Oversized"]),
        ("Curtain Panels", "Meridian Home Goods", 29, 99, ["Blackout", "Sheer"]),
        ("Decorative Tray", "Brightmoor Decor", 14, 39, ["Marble", "Rattan"]),
    ],
    "Kitchen & Dining": [
        ("Cookware Set", "Copperkettle Kitchenware", 129, 349, ["10-Piece", "14-Piece"]),
        ("Chef's Knife", "Copperkettle Kitchenware", 39, 129, ["8-inch"]),
        ("Dinnerware Set", "Meridian Home Goods", 59, 189, ["12-Piece", "16-Piece"]),
        ("Glassware Set", "Meridian Home Goods", 19, 69, ["Tumblers", "Stemware"]),
        ("Air Fryer", "Copperkettle Kitchenware", 59, 179, ["4-Quart", "6-Quart"]),
        ("Stand Mixer", "Copperkettle Kitchenware", 199, 449, ["Tilt-Head", "Bowl-Lift"]),
        ("Coffee Maker", "Copperkettle Kitchenware", 49, 199, ["12-Cup", "Single Serve"]),
        ("Electric Kettle", "Copperkettle Kitchenware", 29, 79, ["Glass", "Steel"]),
        ("Countertop Blender", "Copperkettle Kitchenware", 49, 169, ["Personal", "Pro"]),
        ("Cutting Board Set", "Meridian Home Goods", 19, 59, ["Bamboo", "Walnut"]),
        ("Food Storage Set", "Meridian Home Goods", 19, 59, ["Glass 12-Piece"]),
        ("Flatware Set", "Meridian Home Goods", 29, 99, ["20-Piece", "45-Piece"]),
        ("Cast Iron Skillet", "Copperkettle Kitchenware", 24, 79, ["10-inch", "12-inch"]),
        ("Toaster", "Copperkettle Kitchenware", 29, 99, ["2-Slice", "4-Slice"]),
    ],
    "Bedding & Bath": [
        ("Sheet Set", "Softfold Textiles", 39, 129, ["Queen Percale", "King Percale", "Queen Sateen"]),
        ("Duvet Cover", "Softfold Textiles", 49, 149, ["Queen", "King"]),
        ("Comforter", "Softfold Textiles", 69, 219, ["All-Season Queen", "Down-Alt King"]),
        ("Bed Pillow 2-Pack", "Softfold Textiles", 29, 89, ["Standard", "Cooling"]),
        ("Mattress Topper", "Softfold Textiles", 59, 199, ["Memory Foam Queen"]),
        ("Bath Towel Set", "Softfold Textiles", 29, 99, ["6-Piece", "8-Piece"]),
        ("Bath Mat", "Softfold Textiles", 14, 49, ["Plush", "Cotton"]),
        ("Shower Curtain", "Softfold Textiles", 19, 49, ["Waffle", "Printed"]),
        ("Weighted Blanket", "Softfold Textiles", 59, 149, ["15 lb", "20 lb"]),
        ("Throw Blanket", "Softfold Textiles", 24, 79, ["Chunky Knit", "Fleece"]),
    ],
    "Apparel": [
        ("Lounge Set", "Tamarack Apparel Group", 39, 99, ["Women's", "Men's"]),
        ("Linen Shirt", "Tamarack Apparel Group", 34, 79, ["Women's", "Men's"]),
        ("Knit Cardigan", "Tamarack Apparel Group", 44, 109, ["Women's", "Men's"]),
        ("Fleece Pullover", "Tamarack Apparel Group", 39, 89, ["Women's", "Men's"]),
        ("Cotton Robe", "Tamarack Apparel Group", 39, 99, ["Waffle", "Terry"]),
        ("House Slippers", "Tamarack Apparel Group", 19, 49, ["Shearling", "Knit"]),
        ("Everyday Joggers", "Tamarack Apparel Group", 29, 69, ["Women's", "Men's"]),
        ("Cotton Tee 3-Pack", "Tamarack Apparel Group", 24, 54, ["Women's", "Men's"]),
        ("Denim Jacket", "Tamarack Apparel Group", 59, 129, ["Classic", "Cropped"]),
        ("Canvas Tote", "Tamarack Apparel Group", 14, 39, ["Everyday"]),
    ],
    "Outdoor & Garden": [
        ("Patio Dining Set", "Wildfern Outdoor Supply", 399, 899, ["4-Seat", "6-Seat"]),
        ("Lounge Chair", "Wildfern Outdoor Supply", 129, 349, ["Teak", "Woven"]),
        ("Patio Umbrella", "Wildfern Outdoor Supply", 79, 249, ["9-ft", "11-ft"]),
        ("Fire Pit", "Wildfern Outdoor Supply", 149, 449, ["Steel", "Cast Iron"]),
        ("Ceramic Planter Set", "Wildfern Outdoor Supply", 24, 89, ["3-Piece"]),
        ("Solar String Lights", "Wildfern Outdoor Supply", 19, 59, ["24-ft", "48-ft"]),
        ("Garden Tool Kit", "Wildfern Outdoor Supply", 29, 89, ["5-Piece", "9-Piece"]),
        ("Garden Hose", "Wildfern Outdoor Supply", 29, 79, ["50-ft", "100-ft"]),
        ("Grill Cover", "Wildfern Outdoor Supply", 24, 59, ["Medium", "Large"]),
        ("Hammock", "Wildfern Outdoor Supply", 49, 169, ["Single", "Double"]),
        ("Raised Garden Bed", "Wildfern Outdoor Supply", 79, 199, ["4x4", "4x8"]),
    ],
}

# (id, name, start, end, category or None for all, channel, discount, budget)
PROMOS = [
    ("PR-2025-01", "Spring Home Refresh", date(2025, 4, 10), date(2025, 4, 27), "Home Decor", "All", 0.15, 18000),
    ("PR-2025-02", "Mother's Day Gifting", date(2025, 5, 1), date(2025, 5, 11), "Kitchen & Dining", "All", 0.12, 14000),
    ("PR-2025-03", "Memorial Day Sale", date(2025, 5, 22), date(2025, 5, 26), None, "All", 0.20, 42000),
    ("PR-2025-04", "Outdoor Living Event", date(2025, 6, 5), date(2025, 6, 22), "Outdoor & Garden", "All", 0.20, 26000),
    ("PR-2025-05", "Summer Tech Savings", date(2025, 7, 7), date(2025, 7, 20), "Electronics", "All", 0.15, 30000),
    ("PR-2025-06", "Back to School", date(2025, 8, 1), date(2025, 8, 31), "Bedding & Bath", "All", 0.15, 24000),
    ("PR-2025-07", "Labor Day Sale", date(2025, 8, 29), date(2025, 9, 7), None, "All", 0.20, 46000),
    ("PR-2025-08", "Fall Home Refresh", date(2025, 10, 2), date(2025, 10, 19), "Home Decor", "All", 0.15, 20000),
    ("PR-2025-09", "Black Friday Weekend", date(2025, 11, 26), date(2025, 12, 1), None, "All", 0.25, 78000),
    ("PR-2025-10", "Cyber Week Online", date(2025, 12, 1), date(2025, 12, 5), None, "Online", 0.20, 34000),
    ("PR-2025-11", "Holiday Gift Guide", date(2025, 12, 6), date(2025, 12, 21), "Electronics", "All", 0.12, 40000),
    ("PR-2026-01", "Winter Clearance", date(2026, 1, 8), date(2026, 1, 31), None, "All", 0.25, 52000),
    (
        "PR-2026-02",
        "Presidents' Day Furniture Event",
        date(2026, 2, 12),
        date(2026, 2, 16),
        "Furniture",
        "All",
        0.20,
        28000,
    ),
    ("PR-2026-03", "Spring Home Refresh", date(2026, 4, 9), date(2026, 4, 26), "Home Decor", "All", 0.15, 20000),
    ("PR-2026-04", "Mother's Day Gifting", date(2026, 5, 1), date(2026, 5, 10), "Kitchen & Dining", "All", 0.12, 15000),
    ("PR-2026-05", "Memorial Day Sale", date(2026, 5, 21), date(2026, 5, 25), None, "All", 0.20, 45000),
    ("PR-2026-06", "Outdoor Living Event", date(2026, 6, 4), date(2026, 6, 21), "Outdoor & Garden", "All", 0.20, 28000),
    ("PR-2026-07", "Summer Tech Savings", date(2026, 7, 6), date(2026, 7, 19), "Electronics", "All", 0.15, 32000),
    ("PR-2026-08", "Back to School", date(2026, 8, 1), date(2026, 8, 31), "Bedding & Bath", "All", 0.15, 26000),
    ("PR-2026-09", "Labor Day Sale", date(2026, 8, 28), date(2026, 9, 7), None, "All", 0.20, 50000),
    ("PR-2026-10", "Fall Home Refresh", date(2026, 10, 1), date(2026, 10, 18), "Home Decor", "All", 0.15, 22000),
    ("PR-2026-11", "Black Friday Weekend", date(2026, 11, 25), date(2026, 11, 30), None, "All", 0.25, 82000),
    ("PR-2026-12", "Cyber Week Online", date(2026, 11, 30), date(2026, 12, 4), None, "Online", 0.20, 36000),
    ("PR-2026-13", "Holiday Gift Guide", date(2026, 12, 5), date(2026, 12, 20), "Electronics", "All", 0.12, 42000),
]

SEASON = {1: 0.82, 2: 0.86, 3: 0.95, 4: 0.98, 5: 1.02, 6: 1.0, 7: 1.04, 8: 1.12, 9: 1.0, 10: 1.1, 11: 1.3, 12: 1.45}
# Yearly growth of each region's demand, so the regional report has a story.
REGION_GROWTH = {"Northeast": 0.05, "Southeast": -0.02, "Midwest": -0.03, "West": 0.12, "E-commerce": 0.12}
# Category weights of an order line; some categories are seasonal (month -> multiplier).
CATEGORY_WEIGHT = {
    "Electronics": 0.17,
    "Furniture": 0.07,
    "Home Decor": 0.21,
    "Kitchen & Dining": 0.17,
    "Bedding & Bath": 0.14,
    "Apparel": 0.12,
    "Outdoor & Garden": 0.12,
}
CATEGORY_SEASON = {
    "Outdoor & Garden": {
        4: 1.4,
        5: 1.8,
        6: 1.9,
        7: 1.5,
        8: 1.0,
        9: 0.6,
        10: 0.4,
        11: 0.3,
        12: 0.3,
        1: 0.3,
        2: 0.4,
        3: 0.9,
    },
    "Electronics": {11: 1.7, 12: 1.9, 7: 1.3},
    "Bedding & Bath": {8: 1.3, 11: 1.2, 12: 1.2},
}
RETURN_BASE = {
    "Electronics": 0.090,
    "Furniture": 0.050,
    "Home Decor": 0.055,
    "Kitchen & Dining": 0.065,
    "Bedding & Bath": 0.075,
    "Apparel": 0.130,
    "Outdoor & Garden": 0.055,
}
# category -> (opened, unopened, defective) probabilities of a return's condition
RETURN_CONDITION = {
    "Electronics": (0.55, 0.25, 0.20),
    "Furniture": (0.45, 0.25, 0.30),
    "Home Decor": (0.50, 0.40, 0.10),
    "Kitchen & Dining": (0.50, 0.30, 0.20),
    "Bedding & Bath": (0.45, 0.45, 0.10),
    "Apparel": (0.55, 0.40, 0.05),
    "Outdoor & Garden": (0.50, 0.30, 0.20),
}
RETURN_REASONS = {
    "opened": ["Changed mind", "Did not fit the space", "Not as described", "Found a better price", "Gift duplicate"],
    "unopened": ["Changed mind", "Ordered by mistake", "Gift duplicate", "Delivery arrived late"],
    "defective": ["Stopped working", "Arrived damaged", "Missing parts", "Quality below expectation"],
}


def _price_points(low: float, high: float, n: int) -> list[float]:
    if n == 1:
        return [float(round((low + high) / 2) - 0.01)]
    return [float(round((low + (high - low) * i / (n - 1)) / 5) * 5 - 0.01) for i in range(n)]


def make_stores() -> pd.DataFrame:
    rows = [(*s[:8],) for s in STORES] + [(*WEB_STORE[:8],)]
    return pd.DataFrame(
        rows,
        columns=["store_id", "store_name", "store_type", "region", "state", "city", "opened_on", "square_feet"],
    )


def make_products(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    pid = 0
    for category, items in CATALOG.items():
        for sub, supplier, low, high, variants in items:
            prices = _price_points(low, high, len(variants))
            for variant, price in zip(variants, prices, strict=True):
                pid += 1
                private_label = rng.random() < 0.3
                brand = "Lumen Living" if private_label else SUPPLIERS[supplier]
                margin = float(rng.uniform(0.36, 0.58) if category != "Electronics" else rng.uniform(0.22, 0.38))
                rows.append(
                    {
                        "product_id": f"P{pid:03d}",
                        "sku": f"LRG-{category[:3].upper().replace(' ', '')}-{pid * 37 % 9000 + 1000}",
                        "product_name": f"{brand} {sub} {variant}",
                        "category": category,
                        "subcategory": sub,
                        "brand": brand,
                        "supplier": supplier,
                        "list_price": price,
                        "unit_cost": round(price * (1 - margin), 2),
                    }
                )
    df = pd.DataFrame(rows)
    if df["product_name"].duplicated().any():
        raise ValueError("duplicate product names")
    return df


def _season_array(days: list[date]) -> np.ndarray:
    return np.array([SEASON[d.month] for d in days])


def _promo_mult_array(days: list[date]) -> np.ndarray:
    mult = np.ones(len(days))
    for _, _, start, end, category, _, _, _ in PROMOS:
        for i, d in enumerate(days):
            if start <= d <= end:
                mult[i] = max(mult[i], 1.35 if category is None else 1.15)
    return mult


def make_promo_calendar() -> pd.DataFrame:
    rows = []
    for pid, name, start, end, category, channel, disc, budget in PROMOS:
        status = "Completed" if end < AS_OF else ("Active" if start <= AS_OF else "Planned")
        rows.append(
            {
                "promo_id": pid,
                "promo_name": name,
                "start_date": start,
                "end_date": end,
                "category": category or "All categories",
                "channel": channel,
                "discount_pct": disc,
                "budget_usd": budget,
                "status": status,
            }
        )
    return pd.DataFrame(rows)


def generate(seed: int = SEED) -> dict[str, pd.DataFrame]:
    streams = np.random.SeedSequence(seed).spawn(6)
    r_prod, r_cust, r_ord, r_item, r_ret, r_plan = (np.random.default_rng(s) for s in streams)

    stores = make_stores()
    products = make_products(r_prod)
    store_rows = {s[0]: s for s in STORES}
    phys_ids = [s[0] for s in STORES]
    phys_weight = np.array([s[8] for s in STORES])
    phys_region = {s[0]: s[3] for s in STORES}
    phys_open = {s[0]: s[6] for s in STORES}
    phys_type = {s[0]: s[2] for s in STORES}

    days = [WINDOW_START + timedelta(days=i) for i in range((WINDOW_END - WINDOW_START).days + 1)]
    n_days = len(days)
    season = _season_array(days)
    promo_mult = _promo_mult_array(days)
    year_frac = np.arange(n_days) / 365.0
    growth = {reg: (1.0 + g) ** year_frac for reg, g in REGION_GROWTH.items()}

    # ---------------------------------------------------------------- customers
    n = N_CUSTOMERS
    in_window = r_cust.random(n) < 0.32
    window_days = (date(2026, 9, 10) - WINDOW_START).days
    pre_days = (WINDOW_START - date(2021, 1, 1)).days
    # in-window sign-ups arrive a little faster over time
    join_offset = np.where(
        in_window,
        (window_days * np.sqrt(r_cust.random(n))).astype(int),
        -(r_cust.integers(1, pre_days + 1, n)),
    )
    join_offset = np.sort(join_offset)  # customer ids grow with sign-up date
    joined_dates = [WINDOW_START + timedelta(days=int(o)) for o in join_offset]

    z = r_cust.normal(size=n)
    q_gold, q_silver = np.quantile(z, [0.88, 0.60])
    tier = np.where(z >= q_gold, "gold", np.where(z >= q_silver, "silver", "bronze"))
    for i, jd in enumerate(joined_dates):
        if jd >= date(2026, 8, 1):
            tier[i] = "bronze"
        elif jd >= date(2026, 4, 1) and tier[i] == "gold":
            tier[i] = "silver"
    base_rate = np.select([tier == "gold", tier == "silver"], [0.62, 0.34], default=0.16) * r_cust.lognormal(
        0.0, 0.45, n
    )
    base_rate *= ORDER_RATE_SCALE
    hazard = np.select([tier == "gold", tier == "silver"], [0.012, 0.030], default=0.055) / 30.4

    web_first = r_cust.random(n) < 0.20
    home_store = []
    for i in range(n):
        if web_first[i]:
            home_store.append("WEB")
            continue
        open_mask = np.array([phys_open[s] <= joined_dates[i] for s in phys_ids])
        w = phys_weight * open_mask
        home_store.append(str(r_cust.choice(phys_ids, p=w / w.sum())))
    home_region = np.array([phys_region.get(h, "E-commerce") for h in home_store])
    age_band = r_cust.choice(
        ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"], p=[0.08, 0.26, 0.25, 0.18, 0.14, 0.09], size=n
    )
    email_opt_in = np.where(web_first, r_cust.random(n) < 0.88, r_cust.random(n) < 0.68)
    ret_prop = r_cust.lognormal(-0.1, 0.45, n) * np.where(r_cust.random(n) < 0.12, 3.0, 1.0)

    customers = pd.DataFrame(
        {
            "customer_id": [f"C{i + 1:05d}" for i in range(n)],
            "tier": tier,
            "joined_at": joined_dates,
            "home_store_id": home_store,
            "age_band": age_band,
            "email_opt_in": email_opt_in,
        }
    )

    # ---------------------------------------------------------------- order days
    start_idx = np.maximum(join_offset, 0)
    end_idx = start_idx + np.floor(r_ord.exponential(1.0 / hazard)).astype(int)
    d_idx = np.arange(n_days)
    ramp = np.clip((end_idx[:, None] - d_idx[None, :]) / 90.0, 0.0, 1.0)
    active = (d_idx[None, :] >= start_idx[:, None]) & (d_idx[None, :] < end_idx[:, None])
    reg_growth = np.stack([growth[r] for r in home_region])
    p = (base_rate / 30.4)[:, None] * season[None, :] * reg_growth * promo_mult[None, :] * (0.35 + 0.65 * ramp) * active
    first_day = np.nonzero(join_offset >= 0)[0]
    for c in first_day:
        if start_idx[c] < n_days:
            p[c, start_idx[c]] = 0.85
    p = np.clip(p, 0.0, 0.6)
    cust_idx, day_idx = np.nonzero(r_ord.random(p.shape) < p)

    # ---------------------------------------------------------------- orders and items
    by_cat = {c: products[products["category"] == c].reset_index(drop=True) for c in CATEGORIES}
    pop = {c: r_prod.pareto(1.3, len(by_cat[c])) + 1.0 for c in CATEGORIES}
    promos_df = make_promo_calendar()
    promo_rows = list(PROMOS)

    order_rows: list[dict] = []
    for k in range(len(cust_idx)):
        c = int(cust_idx[k])
        d = days[int(day_idx[k])]
        online = r_ord.random() < (0.90 if web_first[c] else 0.07)
        if online:
            store = "WEB"
        else:
            u = r_ord.random()
            hs = home_store[c]
            if hs != "WEB" and u < 0.85:
                store = hs
            else:
                reg = phys_region.get(hs) if hs != "WEB" else None
                cand = [s for s in phys_ids if phys_open[s] <= d and (reg is None or phys_region[s] == reg)]
                w = np.array([store_rows[s][8] for s in cand])
                store = str(r_ord.choice(cand, p=w / w.sum()))
        hour_w = np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 5, 7, 8, 8, 7, 7, 8, 8, 7, 5, 3, 1, 0, 0], dtype=float)
        if online:
            hour_w = np.array([1, 1, 0, 0, 0, 0, 1, 2, 3, 4, 4, 5, 5, 4, 4, 4, 5, 6, 7, 8, 8, 7, 5, 3], dtype=float)
        hour = int(r_ord.choice(24, p=hour_w / hour_w.sum()))
        ts = datetime(d.year, d.month, d.day, hour, int(r_ord.integers(0, 60)), int(r_ord.integers(0, 60)))
        # promo applied?
        promo = None
        for pr in promo_rows:
            if pr[2] <= d <= pr[3] and (pr[5] == "All" or (pr[5] == "Online") == online):
                if r_ord.random() < 0.6 and (promo is None or pr[6] > promo[6]):
                    promo = pr
        n_items = int(r_item.choice([1, 2, 3, 4], p=[0.64, 0.22, 0.09, 0.05]))
        lines: dict[str, dict] = {}
        for _ in range(n_items):
            w = np.array(
                [
                    CATEGORY_WEIGHT[cat]
                    * CATEGORY_SEASON.get(cat, {}).get(d.month, 1.0)
                    * (1.6 if cat == "Furniture" and phys_type.get(store) == "flagship" else 1.0)
                    * (0.55 if cat == "Furniture" and tier[c] == "bronze" else 1.0)
                    for cat in CATEGORIES
                ]
            )
            cat = str(r_item.choice(CATEGORIES, p=w / w.sum()))
            pw = pop[cat] / pop[cat].sum()
            prod = by_cat[cat].iloc[int(r_item.choice(len(pw), p=pw))]
            qty = (
                1
                if (cat in ("Electronics", "Furniture") or r_item.random() < 0.9)
                else int(r_item.choice([2, 3], p=[0.8, 0.2]))
            )
            if prod["product_id"] in lines:
                lines[prod["product_id"]]["quantity"] += qty
            else:
                lines[prod["product_id"]] = {"product": prod, "quantity": qty}
        used_promo = False
        line_list = []
        for ln in lines.values():
            prod = ln["product"]
            qty = ln["quantity"]
            gross = round(qty * float(prod["list_price"]), 2)
            disc = 0.0
            if promo is not None and (promo[4] is None or promo[4] == prod["category"]):
                disc = round(gross * promo[6], 2)
                used_promo = True
            line_list.append((prod, qty, gross, disc))
        order_rows.append(
            {
                "customer_idx": c,
                "customer_id": customers.at[c, "customer_id"],
                "store_id": store,
                "ordered_at": ts,
                "channel": "online" if online else "in_store",
                "promo_id": promo[0] if used_promo else None,
                "lines": line_list,
            }
        )
    order_rows.sort(key=lambda r: (r["ordered_at"], r["customer_id"]))

    orders_out, items_out = [], []
    item_no = 0
    for i, o in enumerate(order_rows):
        oid = f"O{i + 1:06d}"
        gross_total = sum(line[2] for line in o["lines"])
        disc_total = sum(line[3] for line in o["lines"])
        orders_out.append(
            {
                "order_id": oid,
                "customer_id": o["customer_id"],
                "store_id": o["store_id"],
                "ordered_at": o["ordered_at"],
                "channel": o["channel"],
                "promo_id": o["promo_id"],
                "item_count": sum(line[1] for line in o["lines"]),
                "gross_amount": round(gross_total, 2),
                "discount_amount": round(disc_total, 2),
                "net_amount": round(gross_total - disc_total, 2),
            }
        )
        for prod, qty, gross, disc in o["lines"]:
            item_no += 1
            items_out.append(
                {
                    "order_item_id": f"OI{item_no:06d}",
                    "order_id": oid,
                    "product_id": prod["product_id"],
                    "quantity": qty,
                    "unit_price": float(prod["list_price"]),
                    "discount_amount": disc,
                    "line_amount": round(gross - disc, 2),
                }
            )
    orders = pd.DataFrame(orders_out)
    order_items = pd.DataFrame(items_out)

    # ---------------------------------------------------------------- returns
    cust_pos = {cid: i for i, cid in enumerate(customers["customer_id"])}
    prod_cat = products.set_index("product_id")["category"].to_dict()
    ord_info = orders.set_index("order_id")[["customer_id", "store_id", "ordered_at", "channel"]].to_dict("index")
    end_ts = datetime(WINDOW_END.year, WINDOW_END.month, WINDOW_END.day, 23, 59, 59)
    ret_rows = []
    for it in order_items.itertuples(index=False):
        info = ord_info[it.order_id]
        cat = prod_cat[it.product_id]
        pr = RETURN_BASE[cat] * RETURN_SCALE * ret_prop[cust_pos[info["customer_id"]]]
        if info["channel"] == "online":
            pr *= 1.35
        if info["store_id"] in LP_MONITORED_STORES:
            pr *= 3.0
        if (
            info["store_id"] == ELECTRONICS_SPIKE_STORE
            and cat == "Electronics"
            and info["ordered_at"].date() >= ELECTRONICS_SPIKE_START
        ):
            pr *= 9.0
        pr = min(pr, 0.9)
        if r_ret.random() >= pr:
            continue
        delay = 2 + float(r_ret.gamma(2.0, 5.0))
        ret_ts = info["ordered_at"] + timedelta(days=delay)
        ret_ts = ret_ts.replace(
            hour=int(r_ret.integers(9, 21)),
            minute=int(r_ret.integers(0, 60)),
            second=int(r_ret.integers(0, 60)),
            microsecond=0,
        )
        if ret_ts > end_ts or ret_ts.date() <= info["ordered_at"].date():
            continue
        if info["store_id"] == "WEB":
            home = customers.at[cust_pos[info["customer_id"]], "home_store_id"]
            rstore = home if (home != "WEB" and r_ret.random() < 0.2) else "WEB"
        else:
            rstore = info["store_id"]
        part = it.quantity > 1 and r_ret.random() < 0.15
        rqty = 1 if part else it.quantity
        cond = str(r_ret.choice(["opened", "unopened", "defective"], p=RETURN_CONDITION[cat]))
        ret_rows.append(
            {
                "order_item_id": it.order_item_id,
                "order_id": it.order_id,
                "customer_id": info["customer_id"],
                "product_id": it.product_id,
                "store_id": rstore,
                "returned_at": ret_ts,
                "quantity": rqty,
                "returned_amount": round(it.line_amount / it.quantity * rqty, 2),
                "condition": cond,
                "reason": str(r_ret.choice(RETURN_REASONS[cond])),
            }
        )
    ret_rows.sort(key=lambda r: (r["returned_at"], r["order_item_id"]))
    returns = pd.DataFrame(ret_rows)
    returns.insert(0, "return_id", [f"R{i + 1:05d}" for i in range(len(returns))])

    # ---------------------------------------------------------------- merchandising plan
    targets = make_category_targets(r_plan, orders, order_items, products)
    return {
        "stores": stores,
        "products": products,
        "customers": customers,
        "orders": orders,
        "order_items": order_items,
        "returns": returns,
        "category_targets": targets,
        "promo_calendar": promos_df,
    }


QUARTERS = ["2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"]
# Plan against actual for 2026-Q3, so the Q3 review has a story: who beat plan and who missed it.
Q3_2026_PLAN_FACTOR = {
    "Electronics": 1.09,
    "Furniture": 0.96,
    "Home Decor": 0.94,
    "Kitchen & Dining": 1.07,
    "Bedding & Bath": 0.98,
    "Apparel": 1.04,
    "Outdoor & Garden": 0.93,
}


def quarter_start(q: str) -> date:
    year, qn = q.split("-Q")
    return date(int(year), (int(qn) - 1) * 3 + 1, 1)


def make_category_targets(
    rng: np.random.Generator, orders: pd.DataFrame, order_items: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    li = order_items.merge(orders[["order_id", "ordered_at"]], on="order_id").merge(
        products[["product_id", "category"]], on="product_id"
    )
    li["quarter"] = li["ordered_at"].dt.year.astype(str) + "-Q" + li["ordered_at"].dt.quarter.astype(str)
    actual = li.groupby(["quarter", "category"]).agg(sales=("line_amount", "sum"), units=("quantity", "sum"))
    margin_by_cat = {
        "Electronics": 0.29, "Furniture": 0.47, "Home Decor": 0.51, "Kitchen & Dining": 0.45,
        "Bedding & Bath": 0.50, "Apparel": 0.53, "Outdoor & Garden": 0.44,
    }  # fmt: skip
    rows = []
    for q in QUARTERS:
        for cat in CATEGORIES:
            if q in actual.index.get_level_values(0):
                sales, units = float(actual.loc[(q, cat), "sales"]), float(actual.loc[(q, cat), "units"])
            else:  # 2026-Q4 plan: last year's Q2-Q3 trend with the holiday season
                prev = actual.loc[("2026-Q3", cat)]
                sales, units = float(prev["sales"]) * 1.38, float(prev["units"]) * 1.38
            factor = Q3_2026_PLAN_FACTOR[cat] if q == "2026-Q3" else float(rng.uniform(0.92, 1.10))
            if q == "2026-Q4":
                factor = float(rng.uniform(1.0, 1.06))
            target = round(sales * factor / 100.0) * 100.0
            rows.append(
                {
                    "target_id": f"CT-{q.replace('-', '')}-{cat[:3].upper().replace(' ', '')}",
                    "quarter": q,
                    "quarter_start": quarter_start(q),
                    "category": cat,
                    "target_net_sales": float(target),
                    "target_gross_margin_pct": round(margin_by_cat[cat] + float(rng.uniform(-0.01, 0.015)), 3),
                    "target_units": int(round(units * factor / 5.0) * 5),
                    "plan_status": "Locked" if q <= "2026-Q3" else "Draft",
                }
            )
    return pd.DataFrame(rows)
