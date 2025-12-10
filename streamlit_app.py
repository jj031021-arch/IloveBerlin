import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import google.generativeai as genai
import googlemaps

# ---------------------------------------------------------
# 🚨 파일 이름 (GitHub에 올린 엑셀 파일명 그대로!)
# ---------------------------------------------------------
CRIME_FILE_NAME = "2023_berlin_crime.xlsx"

# ---------------------------------------------------------
# 1. 설정 및 API 키
# ---------------------------------------------------------
st.set_page_config(layout="wide", page_title="베를린 통합 지도 가이드")

GMAPS_API_KEY = st.secrets.get("google_maps_api_key", "")
GEMINI_API_KEY = st.secrets.get("gemini_api_key", "")

if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except:
        pass

# ---------------------------------------------------------
# 2. 데이터 처리 (엑셀 읽기 + 오류 해결)
# ---------------------------------------------------------
@st.cache_data
def get_exchange_rate():
    try:
        url = "https://api.exchangerate-api.com/v4/latest/EUR"
        data = requests.get(url).json()
        return data['rates']['KRW']
    except:
        return 1450.0

@st.cache_data
def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&current_weather=true"
        data = requests.get(url).json()
        return data['current_weather']
    except:
        return {"temperature": 15.0, "weathercode": 0}

@st.cache_data
def load_crime_data_for_map(file_name):
    """
    엑셀 파일을 읽어서 지도(Choropleth)에 그릴 수 있는 형태로 가공합니다.
    """
    try:
        # 1. 엑셀 파일 읽기 (앞 4줄 건너뛰기, engine='openpyxl' 필수)
        # sheet_name=None으로 하면 모든 시트를 읽지만, 보통 첫번째 시트에 데이터가 있음
        df = pd.read_excel(file_name, skiprows=4, engine='openpyxl')

        # 2. 컬럼명 정리 (줄바꿈 제거)
        df.columns = [str(c).replace('\n', ' ').strip() for c in df.columns]

        # 3. 필요한 컬럼 찾기 (구 이름, 총 범죄 수)
        district_col = None
        total_col = None
        
        # 파일마다 컬럼명이 미세하게 다를 수 있어 키워드로 찾기
        for c in df.columns:
            if 'Bezeichnung' in c: district_col = c
            if 'Straftaten' in c and 'insgesamt' in c: total_col = c
        
        if not district_col or not total_col:
            return pd.DataFrame()

        # 4. 베를린 12개 구 이름만 필터링 (지도 GeoJSON과 매칭하기 위함)
        berlin_districts = [
            "Mitte", "Friedrichshain-Kreuzberg", "Pankow", "Charlottenburg-Wilmersdorf", 
            "Spandau", "Steglitz-Zehlendorf", "Tempelhof-Schöneberg", "Neukölln", 
            "Treptow-Köpenick", "Marzahn-Hellersdorf", "Lichtenberg", "Reinickendorf"
        ]
        
        # 구 이름이 일치하는 행만 추출
        df = df[df[district_col].isin(berlin_districts)].copy()

        # 5. [중요] 숫자 데이터 정제 (문자 -> 숫자 변환 오류 해결)
        # 엑셀이라 숫자로 잘 들어올 수도 있지만, 혹시 모를 문자 혼입 방지
        df[total_col] = pd.to_numeric(df[total_col], errors='coerce').fillna(0)

        # 6. 컬럼명 통일
        df = df.rename(columns={district_col: 'District', total_col: 'Total_Crime'})
        
        return df[['District', 'Total_Crime']]

    except Exception as e:
        # st.error(f"엑셀 로드 오류: {e}") # 디버깅용
        return pd.DataFrame()

@st.cache_data
def get_osm_places(category, lat, lng, radius_m=3000):
    """OpenStreetMap에서 장소 정보 가져오기"""
    overpass_url = "http://overpass-api.de/api/interpreter"
    
    if category == 'restaurant': tag = '["amenity"="restaurant"]'
    elif category == 'hotel': tag = '["tourism"="hotel"]'
    elif category == 'tourism': tag = '["tourism"~"attraction|museum|artwork|viewpoint"]'
    else: return []

    query = f"""
    [out:json];
    (
      node{tag}(around:{radius_m},{lat},{lng});
    );
    out body;
    """
    try:
        response = requests.get(overpass_url, params={'data': query})
        data = response.json()
        results = []
        for element in data['elements']:
            if 'tags' in element and 'name' in element['tags']:
                name = element['tags']['name']
                # 구글 링크 생성
                search_query = f"{name} Berlin".replace(" ", "+")
                link = f"https://www.google.com/search?q={search_query}"
                
                results.append({
                    "name": name,
                    "lat": element['lat'],
                    "lng": element['lon'],
                    "link": link
                })
        return results
    except: return []

def search_location(query):
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {'q': query, 'format': 'json', 'limit': 1}
        headers = {'User-Agent': 'BerlinApp/1.0'}
        res = requests.get(url, params=params, headers=headers).json()
        if res:
            return float(res[0]['lat']), float(res[0]['lon']), res[0]['display_name']
    except: pass
    return None, None, None

def get_gemini_response(prompt):
    if not GEMINI_API_KEY: return "API 키가 필요합니다."
    try:
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        return response.text
    except: return "AI 오류"

# ---------------------------------------------------------
# 3. 메인 화면 구성
# ---------------------------------------------------------
st.title("🇩🇪 베를린 통합 여행 지도")
st.caption("2023년 범죄 데이터(엑셀)를 활용한 안전 여행 가이드")

# 세션 초기화
if 'reviews' not in st.session_state: st.session_state['reviews'] = {}
if 'recommendations' not in st.session_state: st.session_state['recommendations'] = []
if 'messages' not in st.session_state: st.session_state['messages'] = []
if 'map_center' not in st.session_state: st.session_state['map_center'] = [52.5200, 13.4050]
if 'search_marker' not in st.session_state: st.session_state['search_marker'] = None

# 상단 정보 (환율/날씨)
col1, col2 = st.columns(2)
with col1:
    rate = get_exchange_rate()
    st.metric("💶 유로 환율", f"{rate:.0f}원")
with col2:
    w = get_weather()
    st.metric("⛅ 베를린 날씨", f"{w['temperature']}°C")

st.divider()

# --- 사이드바 설정 ---
st.sidebar.title("🛠️ 지도 필터 & 설정")

# 검색
st.sidebar.subheader("📍 장소 이동")
search_query = st.sidebar.text_input("지역/장소 검색", placeholder="예: Kreuzberg")
if search_query:
    lat, lng, name = search_location(search_query + " Berlin")
    if lat:
        st.session_state['map_center'] = [lat, lng]
        st.session_state['search_marker'] = {"lat": lat, "lng": lng, "name": name}
        st.sidebar.success(f"이동: {name}")

st.sidebar.divider()

# ★★★ 핵심: 레이어 필터 ★★★
st.sidebar.subheader("👀 지도에 표시할 정보")
show_crime = st.sidebar.checkbox("🚨 범죄 위험도 (구역별 색상)", value=True)
st.sidebar.caption("범죄 발생이 많을수록 지도 구역이 빨간색으로 변합니다.")
st.sidebar.write("---")
show_food = st.sidebar.checkbox("🍽️ 주변 맛집", value=True)
show_hotel = st.sidebar.checkbox("🏨 숙박시설", value=False)
show_tour = st.sidebar.checkbox("📸 관광명소", value=False)

# 탭 구성
tab1, tab2, tab3 = st.tabs(["🗺️ 통합 지도", "💬 커뮤니티 (추천/후기)", "🤖 AI 가이드"])

# =========================================================
# TAB 1: 통합 지도 (범죄 + POI)
# =========================================================
with tab1:
    center = st.session_state['map_center']
    m = folium.Map(location=center, zoom_start=13)

    # 1. 범죄 데이터 레이어 (Choropleth Map)
    if show_crime:
        crime_df = load_crime_data_for_map(CRIME_FILE_NAME)
        
        if not crime_df.empty:
            # GeoJSON (베를린 구 경계 - 인터넷에서 자동 로드)
            geo_url = "https://raw.githubusercontent.com/funkeinteraktiv/Berlin-Geodaten/master/berlin_bezirke.geojson"
            
            folium.Choropleth(
                geo_data=geo_url,
                name="범죄 위험도",
                data=crime_df,
                columns=["District", "Total_Crime"],
                key_on="feature.properties.name", # GeoJSON의 구 이름 속성과 매칭
                fill_color="YlOrRd", # 노랑 -> 주황 -> 빨강
                fill_opacity=0.5,
                line_opacity=0.2,
                legend_name="2023년 총 범죄 발생 수"
            ).add_to(m)
        else:
            st.error(f"범죄 데이터({CRIME_FILE_NAME})를 읽을 수 없습니다. 파일명을 확인하세요.")

    # 2. 검색 마커
    if st.session_state['search_marker']:
        sm = st.session_state['search_marker']
        folium.Marker([sm['lat'], sm['lng']], popup=sm['name'], icon=folium.Icon(color='red', icon='info-sign')).add_to(m)

    # 3. 장소 마커 (OSM)
    # 맛집
    if show_food:
        places = get_osm_places('restaurant', center[0], center[1])
        fg_food = folium.FeatureGroup(name="맛집")
        for p in places:
            html = f"<div style='width:150px'><b>{p['name']}</b><br><a href='{p['link']}' target='_blank'>구글 검색</a></div>"
            folium.CircleMarker(
                [p['lat'], p['lng']], radius=5, color='green', fill=True, popup=html
            ).add_to(fg_food)
        fg_food.add_to(m)

    # 호텔
    if show_hotel:
        places = get_osm_places('hotel', center[0], center[1])
        fg_hotel = folium.FeatureGroup(name="호텔")
        for p in places:
            html = f"<div style='width:150px'><b>{p['name']}</b><br><a href='{p['link']}' target='_blank'>구글 검색</a></div>"
            folium.Marker(
                [p['lat'], p['lng']], icon=folium.Icon(color='blue', icon='bed', prefix='fa'), popup=html
            ).add_to(fg_hotel)
        fg_hotel.add_to(m)

    # 관광지
    if show_tour:
        places = get_osm_places('tourism', center[0], center[1])
        fg_tour = folium.FeatureGroup(name="관광")
        for p in places:
            html = f"<div style='width:150px'><b>{p['name']}</b><br><a href='{p['link']}' target='_blank'>구글 검색</a></div>"
            folium.Marker(
                [p['lat'], p['lng']], icon=folium.Icon(color='purple', icon='camera', prefix='fa'), popup=html
            ).add_to(fg_tour)
        fg_tour.add_to(m)

    # 지도 출력
    st_folium(m, width="100%", height=600)

# =========================================================
# TAB 2: 커뮤니티
# =========================================================
with tab2:
    st.subheader("🗣️ 여행자 커뮤니티")
    
    with st.form("rec_form", clear_on_submit=True):
        col_a, col_b = st.columns([1, 2])
        with col_a: name = st.text_input("추천 장소명")
        with col_b: desc = st.text_input("추천 이유 (한 줄)")
        if st.form_submit_button("추천하기"):
            st.session_state['recommendations'].insert(0, {"place": name, "desc": desc, "replies": []})
            st.rerun()
    
    st.write("---")
    
    if st.session_state['recommendations']:
        for i, rec in enumerate(st.session_state['recommendations']):
            with st.container():
                st.markdown(f"**📍 {rec['place']}**")
                st.success(f"{rec['desc']}")
                
                for reply in rec['replies']:
                    st.caption(f"↳ {reply}")
                
                with st.expander("💬 댓글 달기"):
                    r_text = st.text_input("내용", key=f"reply_in_{i}")
                    if st.button("등록", key=f"reply_btn_{i}"):
                        rec['replies'].append(r_text)
                        st.rerun()
                st.divider()
    else:
        st.info("아직 추천 장소가 없습니다. 첫 번째 추천을 남겨보세요!")

# =========================================================
# TAB 3: AI 가이드
# =========================================================
with tab3:
    st.subheader("🤖 Gemini 여행 비서")
    chat_area = st.container(height=500)
    for msg in st.session_state['messages']:
        chat_area.chat_message(msg['role']).write(msg['content'])
    if prompt := st.chat_input("질문하세요..."):
        st.session_state['messages'].append({"role": "user", "content": prompt})
        chat_area.chat_message("user").write(prompt)
        with chat_area.chat_message("assistant"):
            resp = get_gemini_response(prompt)
            st.write(resp)
        st.session_state['messages'].append({"role": "assistant", "content": resp})
