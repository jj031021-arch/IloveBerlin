import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import google.generativeai as genai
import googlemaps
import plotly.express as px
import streamlit.components.v1 as components

# ---------------------------------------------------------
# 🚨 파일 이름 설정 (엑셀 파일명 그대로!)
# ---------------------------------------------------------
CRIME_FILE_NAME = "2023_berlin_crime.xlsx" 

# ---------------------------------------------------------
# 1. 설정 및 API 키 로드
# ---------------------------------------------------------
st.set_page_config(layout="wide", page_title="베를린 풀코스 가이드")

GMAPS_API_KEY = st.secrets.get("google_maps_api_key", "")
GEMINI_API_KEY = st.secrets.get("gemini_api_key", "")

# 클라이언트 초기화
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except:
        pass

# ---------------------------------------------------------
# 2. 데이터 처리 함수
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
def get_osm_places(category, lat, lng, radius_m=3000, cuisine_filter=None):
    overpass_url = "http://overpass-api.de/api/interpreter"
    
    tag = ""
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
                cuisine = element['tags'].get('cuisine', 'general').lower()
                name = element['tags']['name']
                
                place_type = "관광지"
                if category == 'restaurant':
                    if 'korean' in cuisine: place_type = "한식"
                    elif any(x in cuisine for x in ['burger', 'pizza', 'italian', 'french', 'german', 'american', 'steak']): place_type = "양식"
                    elif any(x in cuisine for x in ['chinese', 'vietnamese', 'thai', 'japanese', 'sushi', 'asian', 'indian']): place_type = "아시안"
                    elif any(x in cuisine for x in ['coffee', 'cafe', 'cake']): place_type = "카페"
                    else: place_type = "식당"
                        
                    if cuisine_filter and "전체" not in cuisine_filter: 
                        if place_type not in cuisine_filter: continue
                elif category == 'hotel':
                    place_type = "숙소"

                search_query = f"{name} Berlin".replace(" ", "+")
                google_link = f"https://www.google.com/search?q={search_query}"

                results.append({
                    "name": name,
                    "lat": element['lat'],
                    "lng": element['lon'],
                    "type": category,
                    "desc": place_type, 
                    "link": google_link
                })
        return results
    except Exception:
        return []

@st.cache_data
def load_crime_data_excel(file_name):
    """
    엑셀 파일(.xlsx)을 읽어오는 함수입니다.
    """
    try:
        # 엑셀 파일 읽기 (앞 4줄 건너뛰기 - skiprows=4)
        # engine='openpyxl'은 엑셀 파일을 읽기 위한 도구입니다.
        df = pd.read_excel(file_name, skiprows=4, engine='openpyxl')
            
        # 컬럼명 정리 (\n 제거)
        df.columns = [str(c).replace('\n', ' ').strip() for c in df.columns]
        
        # 구 이름 필터링
        berlin_districts = [
            "Mitte", "Friedrichshain-Kreuzberg", "Pankow", "Charlottenburg-Wilmersdorf", 
            "Spandau", "Steglitz-Zehlendorf", "Tempelhof-Schöneberg", "Neukölln", 
            "Treptow-Köpenick", "Marzahn-Hellersdorf", "Lichtenberg", "Reinickendorf"
        ]
        
        col_name = 'Bezeichnung (Bezirksregion)'
        
        # 파일마다 컬럼명이 다를 수 있어 확인
        if col_name not in df.columns:
            for c in df.columns:
                if 'Bezeichnung' in c:
                    col_name = c
                    break
        
        if col_name in df.columns:
            # 구 이름이 일치하는 행만 남김
            df = df[df[col_name].isin(berlin_districts)]
            df = df.rename(columns={col_name: 'District'})
            return df
        
        return pd.DataFrame()
    except Exception as e:
        # st.error(f"엑셀 파일 읽기 오류: {e}") # 디버깅용
        return pd.DataFrame()

def get_gemini_response(prompt):
    if not GEMINI_API_KEY: return "API 키 확인 필요"
    try:
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        return response.text
    except: return "AI 응답 오류"

def search_location(query):
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {'q': query, 'format': 'json', 'limit': 1}
        headers = {'User-Agent': 'BerlinApp/1.0'}
        res = requests.get(url, params=params, headers=headers).json()
        if res:
            return float(res[0]['lat']), float(res[0]['lon']), res[0]['display_name']
    except:
        pass
    return None, None, None

# ---------------------------------------------------------
# 3. 여행 코스 데이터
# ---------------------------------------------------------
courses = {
    "🌳 Theme 1: 숲과 힐링 (티어가르텐)": [
        {"name": "1. 전승기념탑", "lat": 52.5145, "lng": 13.3501, "type": "view", "desc": "베를린 전경이 한눈에 보이는 황금 천사상"},
        {"name": "2. 티어가르텐 산책", "lat": 52.5135, "lng": 13.3575, "type": "walk", "desc": "도심 속 거대한 허파, 맑은 공기 마시기"},
        {"name": "3. Cafe am Neuen See", "lat": 52.5076, "lng": 13.3448, "type": "food", "desc": "호수 바로 앞, 피자와 맥주가 맛있는 비어가든"},
        {"name": "4. 베를린 동물원", "lat": 52.5079, "lng": 13.3377, "type": "view", "desc": "세계 최대 종을 보유한 역사 깊은 동물원"},
        {"name": "5. Monkey Bar", "lat": 52.5049, "lng": 13.3353, "type": "food", "desc": "동물원 원숭이를 내려다보며 칵테일 한잔"},
        {"name": "6. 카이저 빌헬름 교회", "lat": 52.5048, "lng": 13.3350, "type": "view", "desc": "전쟁의 참상을 기억하기 위해 보존된 교회"}
    ],
    "🎨 Theme 2: 예술과 고전 (박물관 섬)": [
        {"name": "1. 베를린 돔", "lat": 52.5190, "lng": 13.4010, "type": "view", "desc": "웅장한 돔 지붕 위에서 보는 시내 뷰"},
        {"name": "2. 구 국립 미술관", "lat": 52.5208, "lng": 13.3982, "type": "view", "desc": "그리스 신전 같은 외관과 19세기 회화"},
        {"name": "3. James Simon Park", "lat": 52.5213, "lng": 13.4005, "type": "walk", "desc": "슈프레 강변에 앉아 쉬어가는 핫플"},
        {"name": "4. Hackescher Hof", "lat": 52.5246, "lng": 13.4020, "type": "view", "desc": "아르누보 양식의 아름다운 8개 안뜰"},
        {"name": "5. Monsieur Vuong", "lat": 52.5244, "lng": 13.4085, "type": "food", "desc": "줄 서서 먹는 베트남 쌀국수 맛집"},
        {"name": "6. Zeit für Brot", "lat": 52.5265, "lng": 13.4090, "type": "food", "desc": "시나몬 롤이 입에서 녹는 베이커리"}
    ],
    "🏰 Theme 3: 분단의 역사 (장벽 투어)": [
        {"name": "1. 베를린 장벽 기념관", "lat": 52.5352, "lng": 13.3903, "type": "view", "desc": "장벽이 실제 모습 그대로 보존된 곳"},
        {"name": "2. Mauerpark", "lat": 52.5404, "lng": 13.4048, "type": "walk", "desc": "일요일 벼룩시장과 가라오케"},
        {"name": "3. Prater Beer Garden", "lat": 52.5399, "lng": 13.4101, "type": "food", "desc": "베를린에서 가장 오래된 야외 맥주집"},
        {"name": "4. 체크포인트 찰리", "lat": 52.5074, "lng": 13.3904, "type": "view", "desc": "분단 시절 검문소"},
        {"name": "5. Topography of Terror", "lat": 52.5065, "lng": 13.3835, "type": "view", "desc": "나치 비밀경찰 본부 터 역사관"},
        {"name": "6. Mall of Berlin", "lat": 52.5106, "lng": 13.3807, "type": "food", "desc": "식사와 쇼핑을 해결하는 대형 몰"}
    ],
    "🕶️ Theme 4: 힙스터 성지 (크로이츠베르크)": [
        {"name": "1. 오버바움 다리", "lat": 52.5015, "lng": 13.4455, "type": "view", "desc": "가장 아름다운 붉은 벽돌 다리"},
        {"name": "2. East Side Gallery", "lat": 52.5050, "lng": 13.4397, "type": "walk", "desc": "형제의 키스 그림이 있는 야외 갤러리"},
        {"name": "3. Burgermeister", "lat": 52.5005, "lng": 13.4420, "type": "food", "desc": "다리 밑 공중화장실을 개조한 힙한 버거집"},
        {"name": "4. Markthalle Neun", "lat": 52.5020, "lng": 13.4310, "type": "food", "desc": "트렌디한 실내 시장과 스트릿 푸드"},
        {"name": "5. Voo Store", "lat": 52.5005, "lng": 13.4215, "type": "view", "desc": "패션 피플들의 숨겨진 편집샵"},
        {"name": "6. Landwehr Canal", "lat": 52.4960, "lng": 13.4150, "type": "walk", "desc": "운하를 따라 걷는 평화로운 산책로"}
    ],
    "🛍️ Theme 5: 럭셔리 & 쇼핑 (쿠담)": [
        {"name": "1. KaDeWe 백화점", "lat": 52.5015, "lng": 13.3414, "type": "view", "desc": "유럽 최대 백화점"},
        {"name": "2. Kurfürstendamm", "lat": 52.5028, "lng": 13.3323, "type": "walk", "desc": "베를린의 샹젤리제 명품 거리"},
        {"name": "3. Bikini Berlin", "lat": 52.5055, "lng": 13.3370, "type": "view", "desc": "동물원이 보이는 독특한 쇼핑몰"},
        {"name": "4. C/O Berlin", "lat": 52.5065, "lng": 13.3325, "type": "view", "desc": "사진 예술 전문 미술관"},
        {"name": "5. Schwarzes Café", "lat": 52.5060, "lng": 13.3250, "type": "food", "desc": "24시간 영업하는 예술가들의 아지트"},
        {"name": "6. Savignyplatz", "lat": 52.5060, "lng": 13.3220, "type": "walk", "desc": "고풍스러운 서점과 카페 광장"}
    ],
    "🌙 Theme 6: 화려한 밤 (미테 & 야경)": [
        {"name": "1. TV Tower", "lat": 52.5208, "lng": 13.4094, "type": "view", "desc": "베를린 가장 높은 곳에서 야경 감상"},
        {"name": "2. Rosenthaler Str.", "lat": 52.5270, "lng": 13.4020, "type": "walk", "desc": "트렌디한 샵과 갤러리 골목"},
        {"name": "3. Clärchens Ballhaus", "lat": 52.5265, "lng": 13.3965, "type": "food", "desc": "100년 넘은 무도회장에서 식사"},
        {"name": "4. House of Small Wonder", "lat": 52.5240, "lng": 13.3920, "type": "food", "desc": "식물원 같은 인테리어의 브런치"},
        {"name": "5. Friedrichstadt-Palast", "lat": 52.5235, "lng": 13.3885, "type": "view", "desc": "라스베가스 스타일의 화려한 쇼"},
        {"name": "6. 브란덴부르크 문", "lat": 52.5163, "lng": 13.3777, "type": "walk", "desc": "밤 조명이 켜진 랜드마크"}
    ]
}

# ---------------------------------------------------------
# 4. 메인 화면 구성
# ---------------------------------------------------------
st.title("🇩🇪 베를린 풀코스 가이드")
st.caption("공식 범죄 지도 & OpenStreetMap 통합 버전")

# 세션 초기화
if 'reviews' not in st.session_state: st.session_state['reviews'] = {}
if 'recommendations' not in st.session_state: st.session_state['recommendations'] = []
if 'messages' not in st.session_state: st.session_state['messages'] = []
if 'map_center' not in st.session_state: st.session_state['map_center'] = [52.5200, 13.4050]
if 'search_marker' not in st.session_state: st.session_state['search_marker'] = None

# [1] 환율 & 날씨
col1, col2 = st.columns(2)
with col1:
    rate = get_exchange_rate()
    st.metric(label="💶 현재 유로 환율", value=f"{rate:.0f}원", delta="1 EUR 기준")
with col2:
    w = get_weather()
    st.metric(label="⛅ 베를린 기온", value=f"{w['temperature']}°C")

st.divider()

# --- 사이드바 ---
st.sidebar.title("🛠️ 여행 도구")

# 1. 검색
st.sidebar.subheader("🔍 장소 찾기 (위치 이동)")
search_query = st.sidebar.text_input("장소 이름 (예: Curry 36)", placeholder="엔터키를 누르면 이동합니다")
if search_query:
    lat, lng, name = search_location(search_query + " Berlin")
    if lat and lng:
        st.session_state['map_center'] = [lat, lng]
        st.session_state['search_marker'] = {"lat": lat, "lng": lng, "name": name}
        st.sidebar.success(f"이동 완료: {name}")
    else:
        st.sidebar.error("장소를 찾을 수 없습니다.")

st.sidebar.divider()

# 2. 필터
st.sidebar.subheader("🗺️ 지도 필터")
st.sidebar.info("범죄 정보는 첫 번째 탭의 공식 지도를 참고하세요.")
show_hotel = st.sidebar.toggle("🏨 숙박시설 (Hotel)", False)
show_tour = st.sidebar.toggle("📸 관광지 (Tourism)", False)

st.sidebar.markdown("**🍽️ 음식점 종류 선택**")
cuisine_options = ["전체", "한식", "양식", "아시안", "카페", "일반/기타"]
selected_cuisines = st.sidebar.multiselect("원하는 종류를 선택하세요", cuisine_options, default=["전체"])

# --- 메인 탭 ---
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ 범죄 지도 & 탐험", "🚩 추천 코스 (6 Themes)", "💬 여행자 수다방", "📊 데이터 분석"])

# =========================================================
# TAB 1: 공식 범죄 지도 (Iframe) + 탐험
# =========================================================
with tab1:
    st.subheader("🚨 베를린 공식 범죄 지도 (Kriminalitätsatlas)")
    st.info("베를린 경찰청에서 제공하는 실시간 인터랙티브 지도입니다.")
    components.iframe("https://www.kriminalitaetsatlas.berlin.de/K-Atlas/atlas.html", height=650, scrolling=True)
    
    st.divider()
    
    st.subheader("🗺️ 내 주변 장소 탐색 (OSM)")
    center = st.session_state['map_center']
    m1 = folium.Map(location=center, zoom_start=13)

    if st.session_state['search_marker']:
        sm = st.session_state['search_marker']
        folium.Marker(
            [sm['lat'], sm['lng']], 
            popup=sm['name'],
            icon=folium.Icon(color='red', icon='info-sign')
        ).add_to(m1)

    if selected_cuisines:
        places = get_osm_places('restaurant', center[0], center[1], 3000, selected_cuisines)
        fg_food = folium.FeatureGroup(name="식당")
        for p in places:
            c_color = 'green'
            if p['desc'] == '한식': c_color = 'red'
            elif p['desc'] == '카페': c_color = 'beige'
            
            popup_html = (
                f"<div style='font-family:sans-serif; width:150px'>"
                f"<b>{p['name']}</b><br>"
                f"<span style='color:gray'>{p['desc']}</span><br>"
                f"<a href='{p['link']}' target='_blank' style='text-decoration:none; color:blue;'>👉 구글 상세정보</a>"
                f"</div>"
            )
            
            folium.Marker(
                [p['lat'], p['lng']], 
                popup=popup_html,
                icon=folium.Icon(color=c_color, icon='cutlery', prefix='fa')
            ).add_to(fg_food)
        fg_food.add_to(m1)

    if show_hotel:
        hotels = get_osm_places('hotel', center[0], center[1], 3000)
        fg_hotel = folium.FeatureGroup(name="호텔")
        for h in hotels:
            popup_html = (
                f"<div style='font-family:sans-serif; width:150px'>"
                f"<b>{h['name']}</b><br>"
                f"<span style='color:gray'>숙소</span><br>"
                f"<a href='{h['link']}' target='_blank' style='text-decoration:none; color:blue;'>👉 구글 상세정보</a>"
                f"</div>"
            )
            folium.Marker(
                [h['lat'], h['lng']], 
                popup=popup_html, 
                icon=folium.Icon(color='blue', icon='bed', prefix='fa')
            ).add_to(fg_hotel)
        fg_hotel.add_to(m1)

    if show_tour:
        tours = get_osm_places('tourism', center[0], center[1], 3000)
        fg_tour = folium.FeatureGroup(name="관광")
        for t in tours:
            popup_html = (
                f"<div style='font-family:sans-serif; width:150px'>"
                f"<b>{t['name']}</b><br>"
                f"<span style='color:gray'>관광지</span><br>"
                f"<a href='{t['link']}' target='_blank' style='text-decoration:none; color:blue;'>👉 구글 상세정보</a>"
                f"</div>"
            )
            folium.Marker(
                [t['lat'], t['lng']], 
                popup=popup_html,
                icon=folium.Icon(color='purple', icon='camera', prefix='fa')
            ).add_to(fg_tour)
        fg_tour.add_to(m1)

    st_folium(m1, width="100%", height=600)

# =========================================================
# TAB 2: 추천 코스
# =========================================================
with tab2:
    st.subheader("🌟 테마별 추천 코스")
    theme_names = list(courses.keys())
    selected_theme = st.radio("테마 선택:", theme_names, horizontal=True)
    c_data = courses[selected_theme]
    
    c_col1, c_col2 = st.columns([1.5, 1])
    
    with c_col1:
        m2 = folium.Map(location=[c_data[2]['lat'], c_data[2]['lng']], zoom_start=13)
        points = []
        for i, item in enumerate(c_data):
            loc = [item['lat'], item['lng']]
            points.append(loc)
            color = 'orange' if item['type'] == 'food' else 'blue'
            icon = 'cutlery' if item['type'] == 'food' else 'camera'
            
            link = f"https://www.google.com/search?q={item['name'].replace(' ', '+')}+Berlin"
            
            popup_html = (
                f"<div style='font-family:sans-serif; width:180px'>"
                f"<b>{i+1}. {item['name']}</b><br>"
                f"{item['desc']}<br>"
                f"<a href='{link}' target='_blank' style='color:blue;'>👉 구글 상세정보</a>"
                f"</div>"
            )
            
            folium.Marker(
                loc, popup=popup_html, tooltip=f"{i+1}. {item['name']}",
                icon=folium.Icon(color=color, icon=icon)
            ).add_to(m2)
        folium.PolyLine(points, color="red", weight=4, opacity=0.7).add_to(m2)
        st_folium(m2, width="100%", height=500)
        
    with c_col2:
        st.markdown(f"### {selected_theme}")
        st.markdown("---")
        for item in c_data:
            icon_str = "🍽️" if item['type'] == 'food' else "📸" if item['type'] == 'view' else "🚶"
            with st.expander(f"{icon_str} {item['name']}", expanded=True):
                st.write(f"_{item['desc']}_")
                q = item['name'].replace(" ", "+") + "+Berlin"
                st.markdown(f"[🔍 구글 검색 바로가기](https://www.google.com/search?q={q})")

# =========================================================
# TAB 3: 수다방 & AI
# =========================================================
with tab3:
    col_chat, col_ai = st.columns([1, 1])
    
    with col_chat:
        st.subheader("💬 장소별 리뷰")
        input_method = st.radio("장소 선택 방식", ["목록에서 선택", "직접 입력하기"], horizontal=True, label_visibility="collapsed")
        all_places_list = sorted(list(set([p['name'].split(". ")[1] if ". " in p['name'] else p['name'] for v in courses.values() for p in v])))
        
        if input_method == "목록에서 선택":
            sel_place = st.selectbox("리뷰할 장소", all_places_list)
        else:
            sel_place = st.text_input("장소 이름 입력")
            
        if sel_place:
            if sel_place not in st.session_state['reviews']:
                st.session_state['reviews'][sel_place] = []

            with st.form("msg_form", clear_on_submit=True):
                txt = st.text_input(f"'{sel_place}' 후기 입력")
                if st.form_submit_button("등록"):
                    st.session_state['reviews'][sel_place].append(txt)
                    st.rerun()
            
            if st.session_state['reviews'][sel_place]:
                st.write("---")
                for i, msg in enumerate(st.session_state['reviews'][sel_place]):
                    c1, c2 = st.columns([8, 1])
                    c1.info(f"🗣️ {msg}")
                    if c2.button("🗑️", key=f"del_{sel_place}_{i}"):
                        del st.session_state['reviews'][sel_place][i]
                        st.rerun()

        st.divider()
        
        st.subheader("👍 나만의 장소 추천해요")
        with st.form("recommend_form", clear_on_submit=True):
            rec_place = st.text_input("장소 이름")
            rec_desc = st.text_input("이유 (한 줄)")
            if st.form_submit_button("추천 등록"):
                st.session_state['recommendations'].insert(0, {"place": rec_place, "desc": rec_desc, "replies": []})
                st.rerun()
        
        for i, rec in enumerate(st.session_state['recommendations']):
            st.markdown(f"**{i+1}. {rec['place']}**")
            c1, c2 = st.columns([8, 1])
            c1.success(rec['desc'])
            
            if c2.button("🗑️", key=f"del_rec_{i}"):
                del st.session_state['recommendations'][i]
                st.rerun()

            if 'replies' in rec and rec['replies']:
                for reply in rec['replies']:
                    st.caption(f"↳ 💬 {reply}")

            with st.expander("💬 댓글 달기"):
                reply_txt = st.text_input("댓글 내용", key=f"reply_input_{i}")
                if st.button("등록", key=f"reply_btn_{i}"):
                    if 'replies' not in rec:
                        rec['replies'] = []
                    rec['replies'].append(reply_txt)
                    st.rerun()
            st.write("---")

    with col_ai:
        st.subheader("🤖 Gemini 가이드")
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

# =========================================================
# TAB 4: 범죄 통계 분석
# =========================================================
with tab4:
    st.header("📊 범죄 통계 상세 분석")
    st.caption("GitHub에 업로드된 엑셀/CSV 파일을 기반으로 분석합니다.")
    
    # 데이터 로드 시도
    raw_df = load_crime_data_excel(CRIME_FILE_NAME)

    if not raw_df.empty:
        st.success(f"📂 파일 로드 성공: {CRIME_FILE_NAME}")
        
        c_filter1, c_filter2 = st.columns(2)
        with c_filter1:
            st.info("📅 2023년 데이터 기준")
        with c_filter2:
            districts = sorted(raw_df['District'].unique())
            selected_districts = st.multiselect("🏙️ 구(District) 선택", districts, default=districts)
        
        df_display = raw_df.copy()
        if selected_districts:
            df_display = df_display[df_display['District'].isin(selected_districts)]
        
        # 범죄 유형 컬럼 자동 감지
        cols_to_exclude = ['District', 'LOR-Schlüssel (Bezirksregion)', 'Total_Crime']
        crime_types = [c for c in df_display.select_dtypes(include=['number']).columns if c not in cols_to_exclude and 'insgesamt' not in c]
        
        # 숫자형 변환 및 합계 계산
        for c in crime_types:
            df_display[c] = pd.to_numeric(df_display[c], errors='coerce').fillna(0)

        total_crimes = df_display[crime_types].sum().sum()
        
        if not df_display.empty and total_crimes > 0:
            most_crime_district = df_display.groupby('District')[crime_types].sum().sum(axis=1).idxmax()
            most_common_crime = df_display[crime_types].sum().idxmax()
            
            st.markdown("### 📌 핵심 지표")
            kpi1, kpi2, kpi3 = st.columns(3)
            kpi1.metric("분석 대상 총 범죄", f"{int(total_crimes):,}건")
            kpi2.metric("최다 발생 지역", most_crime_district)
            kpi3.metric("최다 빈번 범죄 유형", most_common_crime)
            
            st.divider()

            chart_col1, chart_col2 = st.columns(2)
            
            with chart_col1:
                st.subheader("🏙️ 구별 범죄 순위")
                district_sum = df_display.groupby('District')[crime_types].sum().sum(axis=1).reset_index(name='Count').sort_values('Count', ascending=True)
                fig_bar = px.bar(district_sum, x='Count', y='District', orientation='h', text='Count', color='Count', color_continuous_scale='Reds')
                fig_bar.update_traces(texttemplate='%{text:.2s}', textposition='outside')
                st.plotly_chart(fig_bar, use_container_width=True)
                
            with chart_col2:
                st.subheader("🥧 범죄 유형 비율")
                type_sum = df_display[crime_types].sum().reset_index(name='Count').rename(columns={'index': 'Type'})
                type_sum = type_sum.sort_values('Count', ascending=False).head(10)
                fig_pie = px.pie(type_sum, values='Count', names='Type', hole=0.4)
                fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.warning("선택된 지역의 데이터가 없거나 0건입니다.")
            
    else:
        st.warning("⚠️ 통계 파일을 찾지 못했습니다.")
        st.info(f"GitHub에 '{CRIME_FILE_NAME}' 파일이 업로드되어 있는지 확인해주세요.")
