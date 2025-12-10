import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import requests
import google.generativeai as genai
import googlemaps
import plotly.express as px

# ---------------------------------------------------------
# 🚨 파일 이름 설정 (업로드한 엑셀 파일명 정확히 입력)
# ---------------------------------------------------------
CRIME_FILE_NAME = "2023_berlin_crime.xlsx"

# ---------------------------------------------------------
# 1. 설정 및 API 키
# ---------------------------------------------------------
st.set_page_config(layout="wide", page_title="베를린 통합 가이드")

GMAPS_API_KEY = st.secrets.get("google_maps_api_key", "")
GEMINI_API_KEY = st.secrets.get("gemini_api_key", "")

# AI 초기화
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except:
        pass

# ---------------------------------------------------------
# 2. 데이터 처리 (엑셀 읽기 + 전처리)
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
def load_crime_data(file_name):
    """
    엑셀 파일을 읽어서 지도/통계용 데이터프레임으로 변환
    """
    try:
        # 1. 엑셀 읽기 (앞 4줄 건너뛰기)
        df = pd.read_excel(file_name, skiprows=4, engine='openpyxl')

        # 2. 컬럼명 정리 (\n 제거)
        df.columns = [str(c).replace('\n', ' ').strip() for c in df.columns]

        # 3. 구 이름 확인
        # 파일마다 컬럼명이 다를 수 있어 'Bezeichnung'이 포함된 컬럼 찾기
        district_col = None
        for c in df.columns:
            if 'Bezeichnung' in c:
                district_col = c
                break
        
        if not district_col: return pd.DataFrame()

        # 4. 베를린 12개 구 이름만 필터링 (지도 GeoJSON 매칭용)
        berlin_districts = [
            "Mitte", "Friedrichshain-Kreuzberg", "Pankow", "Charlottenburg-Wilmersdorf", 
            "Spandau", "Steglitz-Zehlendorf", "Tempelhof-Schöneberg", "Neukölln", 
            "Treptow-Köpenick", "Marzahn-Hellersdorf", "Lichtenberg", "Reinickendorf"
        ]
        df = df[df[district_col].isin(berlin_districts)].copy()
        
        # 5. 숫자 변환 (1.000 -> 1000, 빈칸 -> 0)
        # 범죄 유형 컬럼들(숫자형)만 골라서 처리
        cols_to_clean = [c for c in df.columns if c not in [district_col, 'LOR-Schlüssel (Bezirksregion)']]
        for c in cols_to_clean:
            df[c] = df[c].astype(str).str.replace('.', '', regex=False)
            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

        # 6. 컬럼명 표준화 (District)
        df = df.rename(columns={district_col: 'District'})
        
        # 총계 컬럼 찾기 ('insgesamt'가 포함된 컬럼)
        total_col = [c for c in df.columns if 'insgesamt' in c and 'Straftaten' in c]
        if total_col:
            df['Total_Crime'] = df[total_col[0]]
        else:
            # 없으면 숫자 컬럼 다 더해서 만듦
            df['Total_Crime'] = df[cols_to_clean].sum(axis=1)

        return df

    except Exception:
        return pd.DataFrame()

@st.cache_data
def get_osm_places(category, lat, lng, radius_m=3000):
    """OSM 장소 데이터 가져오기"""
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
                # 구글 링크
                search_query = f"{name} Berlin".replace(" ", "+")
                link = f"https://www.google.com/search?q={search_query}"
                
                # 설명 텍스트
                desc = "장소"
                if category == 'restaurant': 
                    cuisine = element['tags'].get('cuisine', '일반')
                    desc = f"맛집 ({cuisine})"
                elif category == 'hotel': desc = "숙박시설"
                elif category == 'tourism': desc = "관광명소"

                results.append({
                    "name": name, "lat": element['lat'], "lng": element['lon'],
                    "type": category, "desc": desc, "link": link
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
    except: return "AI 서비스 오류"

# ---------------------------------------------------------
# 3. 데이터 정의 (여행 코스)
# ---------------------------------------------------------
courses = {
    "🌳 Theme 1: 숲과 힐링": [
        {"name": "1. 전승기념탑", "lat": 52.5145, "lng": 13.3501, "desc": "베를린 전경이 한눈에 보이는 황금 천사상"},
        {"name": "2. 티어가르텐 산책", "lat": 52.5135, "lng": 13.3575, "desc": "도심 속 거대한 허파"},
        {"name": "3. Cafe am Neuen See (점심)", "lat": 52.5076, "lng": 13.3448, "desc": "호수 앞 비어가든 (피자/맥주)"},
        {"name": "4. 베를린 동물원", "lat": 52.5079, "lng": 13.3377, "desc": "세계 최대 종을 보유한 동물원"},
        {"name": "5. 카이저 빌헬름 교회", "lat": 52.5048, "lng": 13.3350, "desc": "전쟁의 상처를 간직한 교회"}
    ],
    "🎨 Theme 2: 예술과 고전": [
        {"name": "1. 베를린 돔", "lat": 52.5190, "lng": 13.4010, "desc": "웅장한 돔 지붕"},
        {"name": "2. 구 국립 미술관", "lat": 52.5208, "lng": 13.3982, "desc": "고전 예술의 정수"},
        {"name": "3. Monsieur Vuong (맛집)", "lat": 52.5244, "lng": 13.4085, "desc": "유명 베트남 쌀국수 맛집"},
        {"name": "4. Hackescher Hof", "lat": 52.5246, "lng": 13.4020, "desc": "아르누보 양식의 안뜰"},
        {"name": "5. 제임스 사이먼 공원", "lat": 52.5213, "lng": 13.4005, "desc": "강변 산책로"}
    ],
    "🏰 Theme 3: 분단의 역사": [
        {"name": "1. 베를린 장벽 기념관", "lat": 52.5352, "lng": 13.3903, "desc": "장벽의 실제 모습"},
        {"name": "2. Mauerpark", "lat": 52.5404, "lng": 13.4048, "desc": "주말 벼룩시장과 공원"},
        {"name": "3. Prater Beer Garden", "lat": 52.5399, "lng": 13.4101, "desc": "가장 오래된 야외 맥주집"},
        {"name": "4. 체크포인트 찰리", "lat": 52.5074, "lng": 13.3904, "desc": "분단 시절 검문소"},
        {"name": "5. Topography of Terror", "lat": 52.5065, "lng": 13.3835, "desc": "나치 역사관"}
    ],
    "🕶️ Theme 4: 힙스터 성지": [
        {"name": "1. 이스트 사이드 갤러리", "lat": 52.5050, "lng": 13.4397, "desc": "장벽 위 야외 갤러리"},
        {"name": "2. 오버바움 다리", "lat": 52.5015, "lng": 13.4455, "desc": "붉은 벽돌 다리"},
        {"name": "3. Burgermeister (맛집)", "lat": 52.5005, "lng": 13.4420, "desc": "다리 밑 힙한 버거집"},
        {"name": "4. Voo Store", "lat": 52.5005, "lng": 13.4215, "desc": "패션 피플들의 숨겨진 편집샵"},
        {"name": "5. Landwehr Canal", "lat": 52.4960, "lng": 13.4150, "desc": "운하 산책"}
    ],
    "🛍️ Theme 5: 럭셔리 & 쇼핑": [
        {"name": "1. KaDeWe 백화점", "lat": 52.5015, "lng": 13.3414, "desc": "유럽 최대 백화점"},
        {"name": "2. 쿠담 거리", "lat": 52.5028, "lng": 13.3323, "desc": "베를린의 샹젤리제 명품 거리"},
        {"name": "3. Schwarzes Café", "lat": 52.5060, "lng": 13.3250, "desc": "24시간 영업하는 예술가들의 아지트"},
        {"name": "4. C/O Berlin", "lat": 52.5065, "lng": 13.3325, "desc": "사진 예술 전문 미술관"},
        {"name": "5. Savignyplatz", "lat": 52.5060, "lng": 13.3220, "desc": "고풍스러운 서점과 카페 광장"}
    ],
    "🌙 Theme 6: 화려한 밤": [
        {"name": "1. TV타워", "lat": 52.5208, "lng": 13.4094, "desc": "야경 감상"},
        {"name": "2. 로젠탈러 거리", "lat": 52.5270, "lng": 13.4020, "desc": "트렌디한 골목"},
        {"name": "3. Clärchens Ballhaus", "lat": 52.5265, "lng": 13.3965, "desc": "무도회장 분위기 식사"},
        {"name": "4. Friedrichstadt-Palast", "lat": 52.5235, "lng": 13.3885, "desc": "화려한 쇼 관람"},
        {"name": "5. 브란덴부르크 문", "lat": 52.5163, "lng": 13.3777, "desc": "밤 조명이 켜진 랜드마크"}
    ]
}

# ---------------------------------------------------------
# 4. UI 구성
# ---------------------------------------------------------
st.title("🇩🇪 베를린 통합 여행 가이드")
st.caption("2023년 데이터 기반 안전 여행 & 추천 코스")

# 세션 상태
if 'reviews' not in st.session_state: st.session_state['reviews'] = {}
if 'recommendations' not in st.session_state: st.session_state['recommendations'] = []
if 'messages' not in st.session_state: st.session_state['messages'] = []
if 'map_center' not in st.session_state: st.session_state['map_center'] = [52.5200, 13.4050]
if 'search_marker' not in st.session_state: st.session_state['search_marker'] = None

# 상단: 환율 & 날씨
c1, c2 = st.columns(2)
with c1:
    rate = get_exchange_rate()
    st.metric("💶 유로 환율", f"{rate:.0f}원", delta="1 EUR 기준")
with c2:
    w = get_weather()
    st.metric("⛅ 베를린 날씨", f"{w['temperature']}°C")

st.divider()

# --- 사이드바 ---
st.sidebar.title("🛠️ 여행 도구")

# 검색
st.sidebar.subheader("📍 장소 이동")
search_query = st.sidebar.text_input("지역 이름 (예: Mitte)", placeholder="엔터키 입력")
if search_query:
    lat, lng, name = search_location(search_query + " Berlin")
    if lat:
        st.session_state['map_center'] = [lat, lng]
        st.session_state['search_marker'] = {"lat": lat, "lng": lng, "name": name}
        st.sidebar.success(f"이동: {name}")

st.sidebar.divider()

# ★★★ 지도 필터 ★★★
st.sidebar.subheader("👀 지도 필터 (Layer)")
show_crime = st.sidebar.checkbox("🚨 범죄 위험도 (지역별)", value=True)
st.sidebar.caption("켜면 범죄가 많은 지역이 빨간색으로 표시됩니다.")
st.sidebar.write("---")
show_food = st.sidebar.checkbox("🍽️ 주변 맛집", value=True)
show_hotel = st.sidebar.checkbox("🏨 숙박시설", value=False)
show_tour = st.sidebar.checkbox("📸 관광명소", value=False)

# 탭 구성
tab1, tab2, tab3, tab4 = st.tabs(["🗺️ 통합 지도", "🚩 추천 코스", "💬 커뮤니티/AI", "📊 범죄 분석"])

# =========================================================
# TAB 1: 통합 지도 (범죄 + POI)
# =========================================================
with tab1:
    center = st.session_state['map_center']
    m = folium.Map(location=center, zoom_start=13)

    # 1. 범죄 데이터 (Choropleth)
    if show_crime:
        crime_df = load_crime_data(CRIME_FILE_NAME)
        
        if not crime_df.empty:
            # GeoJSON URL (베를린 구 경계)
            geo_url = "https://raw.githubusercontent.com/funkeinteraktiv/Berlin-Geodaten/master/berlin_bezirke.geojson"
            
            folium.Choropleth(
                geo_data=geo_url,
                name="범죄 위험도",
                data=crime_df,
                columns=["District", "Total_Crime"],
                key_on="feature.properties.name",
                fill_color="YlOrRd",
                fill_opacity=0.5,
                line_opacity=0.2,
                legend_name="총 범죄 발생 건수 (2023)"
            ).add_to(m)
        else:
            st.error(f"데이터를 불러올 수 없습니다. 파일명 확인: {CRIME_FILE_NAME}")

    # 2. 검색 마커
    if st.session_state['search_marker']:
        sm = st.session_state['search_marker']
        folium.Marker([sm['lat'], sm['lng']], popup=sm['name'], icon=folium.Icon(color='red', icon='info-sign')).add_to(m)

    # 3. 아이콘 마커 (맛집, 호텔, 관광지)
    # 맛집 (초록, cutlery)
    if show_food:
        places = get_osm_places('restaurant', center[0], center[1])
        fg_food = folium.FeatureGroup(name="맛집")
        for p in places:
            html = f"<div style='width:150px'><b>{p['name']}</b><br><span style='color:grey'>{p['desc']}</span><br><a href='{p['link']}' target='_blank'>구글 검색</a></div>"
            folium.Marker(
                [p['lat'], p['lng']], popup=html, 
                icon=folium.Icon(color='green', icon='cutlery', prefix='fa')
            ).add_to(fg_food)
        fg_food.add_to(m)

    # 호텔 (파랑, bed)
    if show_hotel:
        places = get_osm_places('hotel', center[0], center[1])
        fg_hotel = folium.FeatureGroup(name="호텔")
        for p in places:
            html = f"<div style='width:150px'><b>{p['name']}</b><br><span style='color:grey'>{p['desc']}</span><br><a href='{p['link']}' target='_blank'>구글 검색</a></div>"
            folium.Marker(
                [p['lat'], p['lng']], popup=html, 
                icon=folium.Icon(color='blue', icon='bed', prefix='fa')
            ).add_to(fg_hotel)
        fg_hotel.add_to(m)

    # 관광지 (보라, camera)
    if show_tour:
        places = get_osm_places('tourism', center[0], center[1])
        fg_tour = folium.FeatureGroup(name="관광")
        for p in places:
            html = f"<div style='width:150px'><b>{p['name']}</b><br><span style='color:grey'>{p['desc']}</span><br><a href='{p['link']}' target='_blank'>구글 검색</a></div>"
            folium.Marker(
                [p['lat'], p['lng']], popup=html, 
                icon=folium.Icon(color='purple', icon='camera', prefix='fa')
            ).add_to(fg_tour)
        fg_tour.add_to(m)

    st_folium(m, width="100%", height=600)

# =========================================================
# TAB 2: 추천 코스
# =========================================================
with tab2:
    st.subheader("🚩 테마별 추천 여행 코스")
    
    themes = list(courses.keys())
    selected_theme = st.selectbox("테마를 선택하세요", themes)
    
    course_data = courses[selected_theme]
    
    c_col1, c_col2 = st.columns([2, 1])
    
    with c_col1:
        # 코스 지도
        m2 = folium.Map(location=[course_data[2]['lat'], course_data[2]['lng']], zoom_start=13)
        points = []
        for i, item in enumerate(course_data):
            loc = [item['lat'], item['lng']]
            points.append(loc)
            
            icon_name = 'cutlery' if '맛집' in item.get('desc', '') or '음식' in item.get('desc', '') else 'camera'
            icon_color = 'orange' if icon_name == 'cutlery' else 'blue'
            
            folium.Marker(
                loc, tooltip=f"{i+1}. {item['name']}",
                icon=folium.Icon(color=icon_color, icon=icon_name, prefix='fa')
            ).add_to(m2)
        
        folium.PolyLine(points, color="red", weight=4, opacity=0.7).add_to(m2)
        st_folium(m2, height=400)
        
    with c_col2:
        st.markdown(f"### {selected_theme}")
        for idx, spot in enumerate(course_data):
            with st.expander(f"{idx+1}. {spot['name']}"):
                st.write(spot['desc'])
                q = spot['name'].replace(" ", "+") + "+Berlin"
                st.markdown(f"[🔍 구글 검색](https://www.google.com/search?q={q})")

# =========================================================
# TAB 3: 커뮤니티 & AI
# =========================================================
with tab3:
    c_chat, c_ai = st.columns([1, 1])
    
    # 1. 장소 추천 (대댓글 포함)
    with c_chat:
        st.subheader("👍 나만의 장소 추천")
        
        with st.form("rec_form", clear_on_submit=True):
            name = st.text_input("장소 이름")
            reason = st.text_input("추천 이유")
            if st.form_submit_button("등록"):
                st.session_state['recommendations'].insert(0, {"place": name, "desc": reason, "replies": []})
                st.rerun()
        
        st.write("---")
        
        if st.session_state['recommendations']:
            for i, rec in enumerate(st.session_state['recommendations']):
                st.markdown(f"**📍 {rec['place']}**")
                st.success(rec['desc'])
                
                # 대댓글 표시
                for reply in rec['replies']:
                    st.caption(f"↳ {reply}")
                
                # 대댓글 입력
                with st.expander("💬 댓글 달기"):
                    reply_text = st.text_input("내용", key=f"re_{i}")
                    if st.button("등록", key=f"btn_{i}"):
                        rec['replies'].append(reply_text)
                        st.rerun()
                st.divider()
        else:
            st.info("첫 번째 추천을 남겨보세요!")

    # 2. AI 가이드
    with c_ai:
        st.subheader("🤖 Gemini 여행 비서")
        chat_box = st.container(height=500)
        for msg in st.session_state['messages']:
            chat_box.chat_message(msg['role']).write(msg['content'])
            
        if prompt := st.chat_input("베를린 여행 질문하기..."):
            st.session_state['messages'].append({"role": "user", "content": prompt})
            chat_box.chat_message("user").write(prompt)
            with chat_box.chat_message("assistant"):
                resp = get_gemini_response(prompt)
                st.write(resp)
            st.session_state['messages'].append({"role": "assistant", "content": resp})

# =========================================================
# TAB 4: 범죄 통계 분석 (Interactive)
# =========================================================
with tab4:
    st.header("📊 베를린 범죄 데이터 상세 분석")
    
    df_stat = load_crime_data(CRIME_FILE_NAME)
    
    if not df_stat.empty:
        # 데이터가 있으면 대시보드 표시
        total_crime = df_stat['Total_Crime'].sum()
        max_district = df_stat.loc[df_stat['Total_Crime'].idxmax()]['District']
        
        k1, k2 = st.columns(2)
        k1.metric("총 범죄 발생 건수", f"{int(total_crime):,}건")
        k2.metric("최다 발생 구역", max_district)
        
        st.divider()
        
        # Plotly 차트
        col_chart1, col_chart2 = st.columns(2)
        
        with col_chart1:
            st.subheader("🏙️ 구별 범죄 순위")
            df_sorted = df_stat.sort_values('Total_Crime', ascending=True)
            fig_bar = px.bar(
                df_sorted, x='Total_Crime', y='District', orientation='h',
                text='Total_Crime', title="지역별 범죄 발생 수",
                color='Total_Crime', color_continuous_scale='Reds'
            )
            fig_bar.update_traces(texttemplate='%{text:.2s}', textposition='outside')
            st.plotly_chart(fig_bar, use_container_width=True)
            
        with col_chart2:
            st.subheader("🥧 범죄 유형 분석")
            # 숫자형 컬럼 중 'Total'과 'Code' 등을 제외한 범죄 유형만 추출
            crime_cols = [c for c in df_stat.columns if c not in ['District', 'Total_Crime', 'LOR-Schlüssel (Bezirksregion)']]
            # 문자열이 섞여있을 수 있으므로 다시 한번 숫자 변환 후 합계
            type_sums = df_stat[crime_cols].apply(pd.to_numeric, errors='coerce').sum().sort_values(ascending=False).head(10)
            
            fig_pie = px.pie(
                values=type_sums.values, names=type_sums.index,
                title="상위 10개 범죄 유형", hole=0.4
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig_pie, use_container_width=True)
            
    else:
        st.warning("데이터를 분석할 수 없습니다. 엑셀 파일을 확인해주세요.")
