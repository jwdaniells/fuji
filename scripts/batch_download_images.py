import json, base64, re, time, os, subprocess, datetime
import urllib.request

tok  = os.environ["GH_TOKEN"]
repo = "jwdaniells/fuji"
api  = f"https://api.github.com/repos/{repo}/contents"
hdrs = {"Authorization": f"token {tok}", "Content-Type": "application/json"}

def api_get(path):
    req = urllib.request.Request(api + path, headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def api_put(path, msg, b64, sha=None):
    body = {"message": msg, "content": b64}
    if sha: body["sha"] = sha
    data = json.dumps(body).encode()
    req = urllib.request.Request(api + path, data=data, method="PUT", headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def fetch_curl(url, user_agent=None, extra_headers=None):
    ua = user_agent or "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    cmd = ["curl", "-sL", "--max-time", "25", "-A", ua]
    if extra_headers:
        for h in extra_headers:
            cmd += ["-H", h]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0 and len(result.stdout) > 200:
        return result.stdout
    return None

def get_wayback_url(source_url):
    """Get a recent snapshot URL from the Wayback Machine"""
    wb_api = f"http://archive.org/wayback/available?url={source_url}"
    result = subprocess.run(
        ["curl", "-sL", "--max-time", "10", wb_api],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            snap = data.get("archived_snapshots", {}).get("closest", {})
            if snap.get("available"):
                return snap["url"]
        except:
            pass
    return None

def get_og_image_from_html(html):
    """Extract og:image or first body image from HTML"""
    if not html:
        return None
    
    # og:image meta tag
    for pattern in [
        r'property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    ]:
        m = re.search(pattern, html)
        if m:
            url = m.group(1)
            if "270%2C270" not in url and "cropped" not in url and "app-store" not in url:
                return url
    
    # Body images - fujixweekly wp-content
    skip = ["cropped", "logo", "icon", "270x270", "app-store", "img_5106", "android", "apple-app", "banner", "header"]
    
    # i0.wp.com pattern (WordPress CDN) - convert to direct URL
    for m in re.finditer(r'https://i0\.wp\.com/(fujixweekly\.com/wp-content/uploads/\d{4}/\d{2}/[^?"\s]+\.jpg)', html):
        img_path = m.group(1)
        if not any(s in img_path.lower() for s in skip):
            return f"https://{img_path}"
    
    # Direct wp-content URLs
    for m in re.finditer(r'https://fujixweekly\.com/wp-content/uploads/\d{4}/\d{2}/[^\s"\'<>]+\.jpg', html):
        url = m.group(0)
        if not any(s in url.lower() for s in skip):
            return url
    
    # film.recipes
    for m in re.finditer(r'https://film\.recipes/wp-content/uploads/[^\s"\'<>]+\.jpg', html):
        url = m.group(0)
        if not any(s in url.lower() for s in skip):
            return url
    
    # squarespace/wixstatic
    for m in re.finditer(r'https://(?:images\.squarespace-cdn|static\.wixstatic)\.com/[^\s"\'<>]+\.jpg', html):
        url = m.group(0)
        if not any(s in url.lower() for s in skip):
            return url
    
    return None

def get_image_url_for_page(source_url):
    """Try multiple strategies to get an image URL from a page"""
    
    # Strategy 1: Direct fetch with Googlebot user agent
    for ua in [
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Googlebot-Image/1.0",
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ]:
        html = fetch_curl(source_url, user_agent=ua)
        if html and len(html) > 5000:
            img_url = get_og_image_from_html(html)
            if img_url:
                print(f"  [direct/{ua[:20]}] {img_url[:80]}")
                return img_url
        time.sleep(1)
    
    # Strategy 2: Wayback Machine snapshot
    print(f"  Trying Wayback Machine...")
    wb_url = get_wayback_url(source_url)
    if wb_url:
        print(f"  [wayback] snapshot: {wb_url[:80]}")
        html = fetch_curl(wb_url)
        if html:
            img_url = get_og_image_from_html(html)
            if img_url:
                print(f"  [wayback img] {img_url[:80]}")
                return img_url
    
    return None

def download_image(img_url, referer):
    """Download image via curl"""
    result = subprocess.run([
        "curl", "-sL", "--max-time", "30",
        "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "-H", f"Referer: {referer}",
        "-H", "Accept: image/webp,image/apng,image/*,*/*;q=0.8",
        "-o", "/tmp/recipe_img.jpg",
        img_url
    ], capture_output=True, timeout=35)
    if result.returncode == 0:
        with open("/tmp/recipe_img.jpg", "rb") as f:
            data = f.read()
        if len(data) > 5000:
            return data
    return None

# Load data.json
print("Loading data.json...")
d = api_get("/recipes/data.json")
data = json.loads(base64.b64decode(d["content"].replace("\n", "")))

# Process recipes with no image
to_process = []
seen_urls = {}  # Track URL -> image already processed

for r in data["recipes"]:
    img = r.get("image", "")
    src = r.get("source_url", "")
    if not img and src:
        # If we've already processed this URL, reuse the result
        to_process.append((r["id"], r["name"], src, None))
    elif img and img.startswith("http") and src:
        to_process.append((r["id"], r["name"], src, img))

print(f"Processing {len(to_process)} recipes...")
success = []
no_img = []
url_image_cache = {}  # Cache URL -> image_data to avoid re-downloading same page

for i, (rid, name, src_url, direct_url) in enumerate(to_process):
    print(f"\n[{i+1}/{len(to_process)}] {name}")

    # Check cache for same-URL recipes (e.g. KodaNeg variants)
    cached_data = url_image_cache.get(src_url)
    
    if direct_url:
        img_url = direct_url
        referer = src_url
    elif cached_data:
        img_url, referer = cached_data
        print(f"  [cached] {img_url[:80]}")
    else:
        img_url = get_image_url_for_page(src_url)
        if not img_url:
            print(f"  FAILED: no image found")
            no_img.append(name)
            continue
        referer = "https://fujixweekly.com/" if "fujixweekly" in src_url else src_url
    
    img_data = download_image(img_url, referer)
    if not img_data:
        print(f"  FAILED: download failed ({img_url[:60]})")
        no_img.append(name)
        continue
    
    # Cache for same-URL reuse
    if src_url not in url_image_cache:
        url_image_cache[src_url] = (img_url, referer)
    
    img_path = f"/images/{rid}.jpg"
    img_b64 = base64.b64encode(img_data).decode()
    try:
        try:
            ex = api_get(img_path)
            api_put(img_path, f"Add image: {name}", img_b64, ex["sha"])
        except Exception:
            api_put(img_path, f"Add image: {name}", img_b64)
        print(f"  OK: committed {len(img_data)//1024}KB")
    except Exception as e:
        print(f"  FAILED: commit error: {e}")
        no_img.append(name)
        continue

    for recipe in data["recipes"]:
        if recipe["id"] == rid:
            recipe["image"] = f"{rid}.jpg"
            break

    success.append(name)
    time.sleep(0.5)

print(f"\n\nCommitting data.json ({len(success)} images added)...")
data["last_updated"] = datetime.date.today().isoformat()
new_b64 = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
d2 = api_get("/recipes/data.json")
api_put("/recipes/data.json", f"Add images for {len(success)} recipes", new_b64, d2["sha"])

print(f"\n=== RESULTS ===")
print(f"Succeeded: {len(success)}")
print(f"Failed: {len(no_img)}")
if no_img:
    print("Failed list:")
    for n in no_img:
        print(f"  - {n}")
