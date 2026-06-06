import json, base64, re, time, os
import requests

tok  = os.environ["GH_TOKEN"]
repo = "jwdaniells/fuji"
api  = f"https://api.github.com/repos/{repo}/contents"
hdrs = {"Authorization": f"token {tok}", "Content-Type": "application/json"}

def api_get(path):
    r = requests.get(api + path, headers=hdrs, timeout=30)
    r.raise_for_status()
    return r.json()

def api_put(path, msg, b64, sha=None):
    body = {"message": msg, "content": b64}
    if sha:
        body["sha"] = sha
    r = requests.put(api + path, json=body, headers=hdrs, timeout=30)
    r.raise_for_status()
    return r.json()

PAGE_HDRS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def get_og_image(source_url):
    try:
        r = requests.get(source_url, headers=PAGE_HDRS, timeout=20, allow_redirects=True)
        html = r.text
    except Exception as e:
        print(f"  Page fetch error: {e}")
        return None

    # og:image meta tag
    m = re.search(r'property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
    if m:
        url = m.group(1)
        if "270%2C270" not in url and "cropped" not in url and "app-store" not in url:
            return url

    # Fallback: first large wp-content image
    skip = ["cropped", "logo", "icon", "270x270", "app-store", "img_5106", "android", "apple-app"]
    for img in re.findall(r'https://[^\s"]+/wp-content/uploads/\d{4}/\d{2}/[^\s"]+\.jpg', html):
        if not any(s in img for s in skip):
            return img

    # film.recipes
    for img in re.findall(r'https://film\.recipes/wp-content/uploads/[^\s"]+\.jpg', html):
        if not any(s in img for s in skip):
            return img

    # captnlook wixstatic
    for img in re.findall(r'https://static\.wixstatic\.com/media/[^\s"]+\.jpg', html):
        return img

    return None

def download_image(img_url, referer):
    h = dict(PAGE_HDRS)
    h["Referer"] = referer
    try:
        r = requests.get(img_url, headers=h, timeout=30)
        ct = r.headers.get("Content-Type", "")
        if len(r.content) > 5000 and ("image" in ct or img_url.endswith(".jpg")):
            return r.content
    except Exception as e:
        print(f"  Image download error: {e}")
    return None

# Load data.json
print("Loading data.json...")
d = api_get("/recipes/data.json")
data = json.loads(base64.b64decode(d["content"].replace("\n", "")))

missing = [(r["id"], r["name"], r.get("source_url", ""))
           for r in data["recipes"]
           if not r.get("image") and r.get("source_url")]

print(f"Processing {len(missing)} recipes...")
success = []
no_img = []

for i, (rid, name, src_url) in enumerate(missing):
    print(f"[{i+1}/{len(missing)}] {name}")

    img_url = get_og_image(src_url)
    if not img_url:
        print(f"  No image URL found")
        no_img.append(name)
        time.sleep(0.5)
        continue

    print(f"  -> {img_url[:80]}")
    referer = "https://fujixweekly.com/" if "fujixweekly" in src_url else src_url
    img_data = download_image(img_url, referer)
    if not img_data:
        print(f"  Download failed")
        no_img.append(name)
        time.sleep(0.5)
        continue

    img_path = f"/images/{rid}.jpg"
    img_b64 = base64.b64encode(img_data).decode()
    try:
        try:
            ex = api_get(img_path)
            api_put(img_path, f"Add image: {name}", img_b64, ex["sha"])
        except Exception:
            api_put(img_path, f"Add image: {name}", img_b64)
        print(f"  Committed ({len(img_data)//1024}KB)")
    except Exception as e:
        print(f"  Commit failed: {e}")
        no_img.append(name)
        time.sleep(1)
        continue

    for recipe in data["recipes"]:
        if recipe["id"] == rid:
            recipe["image"] = f"{rid}.jpg"
            break

    success.append(name)
    time.sleep(1.5)

# Save data.json
print(f"\nCommitting data.json ({len(success)} images added)...")
data["last_updated"] = "2026-06-06"
new_b64 = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
d2 = api_get("/recipes/data.json")
api_put("/recipes/data.json", f"Add images for {len(success)} recipes", new_b64, d2["sha"])

print(f"\nDone: {len(success)} succeeded, {len(no_img)} failed")
if no_img:
    print("No image found for:")
    for n in no_img:
        print(f"  - {n}")
