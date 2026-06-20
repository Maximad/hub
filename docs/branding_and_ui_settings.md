# Branding and UI settings

This sprint keeps Hub/Masharib business logic unchanged and moves visual controls into the existing system settings and `MediaAsset` media library.

## Change the logo

1. Open Django admin.
2. Upload the image in **مكتبة الوسائط** (`MediaAsset`).
3. Open **إعدادات النظام**.
4. Choose the uploaded asset in **الشعار**.
5. Optionally choose separate assets for **الأيقونة**, **شعار نقطة البيع**, and **شعار الإيصال**.

If a logo field is empty, templates continue to use the text brand fallback and do not crash.

## Change the menu banner

1. Upload a wide banner image in **مكتبة الوسائط**.
2. Open **إعدادات النظام**.
3. Select it in **بانر المنيو**.
4. Keep **إظهار بانر المنيو** enabled to show it on `/menu/`.

If the banner is empty or disabled, the public menu renders without a banner.

## Change colors

Use **إعدادات النظام → الألوان** and set safe HEX values for:

- **اللون الأساسي** (`primary_color`)
- **اللون المساعد** (`accent_color`)
- page background (`background_color`)
- card/surface background (`surface_color`)
- text (`text_color`)

These values are exposed as CSS variables in the base template, including `--hub-primary`, `--hub-accent`, `--hub-bg`, `--hub-surface`, `--hub-text`, `--hub-border`, `--hub-radius`, `--hub-card-radius`, and `--hub-image-ratio`.

## Assign product images

The product image workflow is intentionally one library plus one relation:

1. Upload the image once in **مكتبة الوسائط**.
2. Edit the product in admin.
3. In the product media inline, link the uploaded asset to the product.
4. Mark the desired image as primary and active.

The inline help text explains: “ارفع الصورة في مكتبة الوسائط ثم اربطها بالمنتج هنا.” Products without images use the menu/POS placeholder.

## Recommended image sizes

- Logo: square PNG/SVG if supported, around **512×512**.
- Menu banner: wide image around **1600×600**.
- Product image: **1200×900** or **1000×1000**.
- Receipt logo: simple high-contrast logo that remains readable when printed.

## Choose compact or comfortable menu layout

In **إعدادات النظام → تخطيط المنيو**:

- Use **comfortable** for more whitespace and larger cards.
- Use **compact** for denser mobile/tablet menus.
- Use **image_grid** when product photography should be emphasized.
- Set **كثافة العرض على الموبايل** to compact to keep mobile cards from filling the screen.
- Set **نسبة صور المنتجات** to 1:1, 4:3, or 3:2 depending on your product image crop.
- Keep **إظهار وصف المنتج على الموبايل** disabled for faster compact browsing.

## Test after appearance changes

After changing branding or layout settings:

1. Load `/menu/` on desktop and mobile widths.
2. Add products, modifiers, and item notes.
3. Open **مراجعة الطلب** and submit a test order in the usual test environment.
4. Load `/staff/pos/` on tablet/desktop and mobile widths.
5. Confirm product images and placeholders render.
6. Confirm cashier, reports, and admin product pages still load.
7. Run the project checks used for release validation.
