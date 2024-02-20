import os
import math
import time
import requests
import concurrent.futures
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ================= 配置区域 =================
# 保存路径
SAVE_PATH = "E:/Map/GoogleSat"

# 下载层级 (0-9级约几百MB，建议先测试)
ZOOM_LEVELS = range(0, 9)

# 下载范围 (Google Maps 的数学极限)
# 注意：谷歌纬度极限是 ±85.05112878，超过这个值瓦片就不存在了
BOUNDS = (-180, -85.05, 180, 85.05)

# 线程数
MAX_WORKERS = 16

# Google 卫星图源
URL_TEMPLATE = "http://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
# ===========================================

session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
session.mount('http://', HTTPAdapter(max_retries=retries, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS))
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
})

def latlon2xy(lat, lon, z):
    """经纬度转 Google Web Mercator 坐标 (带边界保护)"""
    n = 2.0 ** z
    x_val = (lon + 180.0) / 360.0 * n
    
    # 保护：防止纬度超过 Mercator 极限导致 math error
    lat = max(-85.0511, min(85.0511, lat))
    
    try:
        rad = math.radians(lat)
        y_val = (1.0 - math.log(math.tan(rad) + (1 / math.cos(rad))) / math.pi) / 2.0 * n
    except ValueError:
        y_val = 0
    return int(x_val), int(y_val)

def download_task(args):
    x, y_google, z, file_path = args
    url = URL_TEMPLATE.format(x=x, y=y_google, z=z)
    try:
        r = session.get(url, timeout=5)
        if r.status_code == 200:
            with open(file_path, 'wb') as f:
                f.write(r.content)
            return 1
        elif r.status_code in [404, 400]:
            return 0
        else:
            return -1
    except Exception:
        return -1

def generate_mercator_xml(path, min_zoom, max_zoom, ext="jpg"):
    """
    生成 EPSG:3857 XML
    虽然只有 85度，但在 3857 投影定义里，这已经是"全图"了
    """
    max_extent = 20037508.34
    initial_res = 156543.03392804062

    xml_content = f"""<?xml version="1.0" encoding="utf-8" ?>
<TileMap tilemapservice="http://www.osgeo.org/services/tilemapservice.xml" version="1.0.0">
  <Title>Google Maps Max Extent</Title>
  <Abstract>EPSG:3857 Web Mercator (Lat limit ~85.05)</Abstract>
  <SRS>EPSG:3857</SRS>
  <BoundingBox maxx="{max_extent:.6f}" maxy="{max_extent:.6f}" minx="-{max_extent:.6f}" miny="-{max_extent:.6f}" />
  <Origin x="-{max_extent:.6f}" y="-{max_extent:.6f}" />
  <TileFormat extension="{ext}" height="256" mime-type="image/jpeg" width="256" />
  <TileSets profile="global-mercator">
"""
    for z in range(min_zoom, max_zoom + 1):
        units = initial_res / (2 ** z)
        xml_content += f'    <TileSet order="{z}" units-per-pixel="{units:.18f}" />\n'

    xml_content += """  </TileSets>
</TileMap>"""
    
    with open(os.path.join(path, "tilemapresource.xml"), "w", encoding="utf-8") as f:
        f.write(xml_content)
    print(f"XML 生成完毕")

def main():
    if not os.path.exists(SAVE_PATH):
        os.makedirs(SAVE_PATH)

    print(f"开始最大化下载 (Google极限范围)")
    
    tasks = []
    min_lon, min_lat, max_lon, max_lat = BOUNDS

    print("计算任务中...")
    for z in ZOOM_LEVELS:
        n_tiles = 2 ** z
        
        # 计算坐标范围
        x_min, _ = latlon2xy(max_lat, min_lon, z)
        x_max, _ = latlon2xy(min_lat, max_lon, z)
        _, y_min_g = latlon2xy(max_lat, min_lon, z)
        _, y_max_g = latlon2xy(min_lat, max_lon, z)

        # 钳制
        x_min, x_max = max(0, x_min), min(n_tiles - 1, x_max)
        y_start, y_end = min(y_min_g, y_max_g), max(y_min_g, y_max_g)
        y_start, y_end = max(0, y_start), min(n_tiles - 1, y_end)

        z_dir = os.path.join(SAVE_PATH, str(z))
        if not os.path.exists(z_dir): os.makedirs(z_dir)

        for x in range(x_min, x_max + 1):
            x_dir = os.path.join(z_dir, str(x))
            if not os.path.exists(x_dir): os.makedirs(x_dir)

            for y_google in range(y_start, y_end + 1):
                y_tms = (2 ** z - 1) - y_google # 翻转Y轴
                filename = f"{y_tms}.jpg"
                file_path = os.path.join(x_dir, filename)

                if not os.path.exists(file_path):
                    tasks.append((x, y_google, z, file_path))

    total_tasks = len(tasks)
    print(f"任务总数: {total_tasks}")

    if total_tasks > 0:
        downloaded_count = 0
        start_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_task, task): task for task in tasks}
            
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                if future.result() == 1:
                    downloaded_count += 1
                
                if (i + 1) % 100 == 0 or (i + 1) == total_tasks:
                    elapsed = time.time() - start_time
                    speed = (i + 1) / elapsed if elapsed > 0 else 0
                    print(f"\r进度: {(i + 1) / total_tasks * 100:.1f}% | 速度: {speed:.1f} 张/秒", end="")

        print(f"\n下载耗时: {time.time() - start_time:.2f} 秒")
    
    generate_mercator_xml(SAVE_PATH, min(ZOOM_LEVELS), max(ZOOM_LEVELS))

if __name__ == "__main__":
    main()