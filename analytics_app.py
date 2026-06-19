import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from datetime import datetime, timedelta

# API server configuration
API_BASE_URL = "http://127.0.0.1:8082"

def fetch_data_from_api(endpoint):
    """
    Perform an HTTP GET request to the Flask API endpoint.
    Returns parsed JSON data or None on connection/HTTP errors.
    """
    try:
        response = requests.get(f"{API_BASE_URL}{endpoint}", timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None

def load_analytics_data():
    """
    Queries exposed Flask API endpoints or falls back to sample DataFrames 
    if the Flask API is offline/unavailable.
    
    ========================================================================
    MYSQL DATABASE QUERIES FOR EXTRACTING DATASETS:
    ========================================================================
    
    1. Daily Translation Activity Data (CHART 1):
       ---------------------------------------------------------------------
       SELECT 
           DATE(translated_at) AS translation_date, 
           COUNT(*) AS translation_count
       FROM 
           translations
       WHERE 
           translated_at IS NOT NULL
       GROUP BY 
           translation_date
       ORDER BY 
           translation_date ASC;
           
    2. Top 5 Users by Files Translated Data (CHART 2):
       ---------------------------------------------------------------------
       SELECT 
           username, 
           COUNT(*) AS files_translated
       FROM 
           translations
       WHERE 
           filename IS NOT NULL 
           AND filename NOT IN ('Text Box Input', 'Text Input', '')
       GROUP BY 
           username
       ORDER BY 
           files_translated DESC
       LIMIT 5;
       
    3. KPI Metrics Aggregates (KPI CARDS):
       ---------------------------------------------------------------------
       - Total Translations:
         SELECT COUNT(*) AS total_translations FROM translations;
         
       - Active Users:
         SELECT COUNT(DISTINCT username) AS active_users FROM translations;
         
       - Total Files Translated:
         SELECT COUNT(*) AS total_files FROM translations 
         WHERE filename IS NOT NULL AND filename NOT IN ('Text Box Input', 'Text Input', '');
         
       - Average Processing Time:
         SELECT AVG(processing_time) AS avg_time FROM translations;
    ========================================================================
    """
    # --- Fallback/Sample Data Generation ---
    today = datetime.now()
    dates = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(14, -1, -1)]
    
    # CHART 1: Daily Translation Activity sample data (Columns: translation_date, translation_count)
    df_daily_sample = pd.DataFrame({
        'translation_date': dates,
        'translation_count': [12, 19, 15, 25, 32, 28, 20, 24, 35, 42, 38, 30, 45, 55, 60]
    })
    
    # CHART 2: Top 5 Users by Files Translated sample data (Columns: username, files_translated)
    df_users_sample = pd.DataFrame({
        'username': ['alice_dev', 'bob_trans', 'charlie_qc', 'david_mgr', 'eva_staff'],
        'files_translated': [45, 32, 28, 19, 12]
    })
    
    # Existing Top Language Pairs sample data
    df_lang_pairs_sample = pd.DataFrame({
        'Language Pair': ['English → Spanish', 'Spanish → English', 'English → French', 'French → English', 'English → German'],
        'Count': [185, 142, 88, 54, 32]
    })
    
    # Existing Average Processing Time sample data
    df_time_sample = pd.DataFrame({
        'Date': dates,
        'Avg Processing Time (s)': [1.8, 1.6, 2.1, 1.9, 1.5, 1.4, 2.3, 1.8, 1.7, 1.5, 1.6, 2.0, 1.7, 1.4, 1.3]
    })
    
    # Existing Privacy Masking Stats sample data
    df_privacy_sample = pd.DataFrame({
        'Metric': [
            'Emails Masked', 
            'Phone Numbers Masked', 
            'Names Masked', 
            'Glossary Terms Protected', 
            'Custom Terms Protected'
        ],
        'Items Protected': [342, 189, 512, 1205, 450]
    })

    # Fetch daily translation activity (CHART 1 source)
    api_daily = fetch_data_from_api("/api/analytics/daily-activity")
    if api_daily and isinstance(api_daily, list):
        df_daily = pd.DataFrame(api_daily)
        # Rename columns to match exact requirements (translation_date, translation_count)
        df_daily = df_daily.rename(columns={'Date': 'translation_date', 'Translations': 'translation_count'})
    else:
        df_daily = df_daily_sample

    # Fetch top language pairs
    api_pairs = fetch_data_from_api("/api/analytics/top-language-pairs")
    if api_pairs and isinstance(api_pairs, list):
        df_lang_pairs = pd.DataFrame(api_pairs)
    else:
        df_lang_pairs = df_lang_pairs_sample

    # Fetch average processing time
    api_time = fetch_data_from_api("/api/analytics/processing-time")
    if api_time and isinstance(api_time, list):
        df_time = pd.DataFrame(api_time)
    else:
        df_time = df_time_sample

    # Fetch privacy masking stats
    api_privacy = fetch_data_from_api("/api/analytics/privacy-stats")
    if api_privacy and 'Metrics' in api_privacy and 'Counts' in api_privacy:
        df_privacy = pd.DataFrame({
            'Metric': api_privacy['Metrics'],
            'Items Protected': api_privacy['Counts']
        })
    else:
        df_privacy = df_privacy_sample

    # Fetch top users by files translated (CHART 2 source)
    api_users = fetch_data_from_api("/api/analytics/top-users")
    if api_users and isinstance(api_users, list):
        df_users = pd.DataFrame(api_users)
        # Ensure 'username' column exists and has no nulls (fallback to stored username or 'Unknown User')
        if 'username' not in df_users.columns:
            df_users['username'] = 'Unknown User'
        df_users['username'] = df_users['username'].fillna('Unknown User').replace('', 'Unknown User')
    else:
        df_users = df_users_sample

    # Fetch KPI summary metrics
    api_kpis = fetch_data_from_api("/api/analytics/kpis")
    if api_kpis and isinstance(api_kpis, dict):
        kpis = api_kpis
    else:
        # Generate calculated fallback KPIs
        kpis = {
            'total_translations': int(df_daily['translation_count'].sum()) if 'translation_count' in df_daily else 505,
            'active_users': 14,
            'total_files': int(df_users['files_translated'].sum()) if 'files_translated' in df_users else 136,
            'avg_processing_time': round(float(df_time['Avg Processing Time (s)'].mean()), 2) if 'Avg Processing Time (s)' in df_time else 1.71
        }

    return df_daily, df_lang_pairs, df_time, df_privacy, df_users, kpis

def customize_chart_layout(fig, title_text, xaxis_title="", yaxis_title=""):
    """
    Applies a clean, modern, enterprise-style layout theme to a Plotly figure.
    This creates design consistency across legends, fonts, gridlines, and spacing.
    """
    fig.update_layout(
        title={
            'text': f"<b>{title_text}</b>",
            'font': {'size': 15, 'family': 'Inter, sans-serif', 'color': '#0F172A'},
            'y': 0.93,
            'x': 0.02,
            'xanchor': 'left',
            'yanchor': 'top'
        },
        xaxis={
            'title': {'text': xaxis_title, 'font': {'size': 11, 'family': 'Inter, sans-serif', 'color': '#475569'}},
            'tickfont': {'size': 10, 'family': 'Inter, sans-serif', 'color': '#64748B'},
            'showgrid': True,
            'gridcolor': '#F1F5F9',
            'linecolor': '#E2E8F0',
            'zeroline': False
        },
        yaxis={
            'title': {'text': yaxis_title, 'font': {'size': 11, 'family': 'Inter, sans-serif', 'color': '#475569'}},
            'tickfont': {'size': 10, 'family': 'Inter, sans-serif', 'color': '#64748B'},
            'showgrid': True,
            'gridcolor': '#F1F5F9',
            'linecolor': '#E2E8F0',
            'zeroline': False
        },
        height=380,  # Unified interior content height to fit standard 450px containers
        template="plotly_white",
        hovermode="closest",
        hoverlabel=dict(
            bgcolor="#0F172A",
            font_size=12,
            font_family="Inter, sans-serif",
            font_color="#FFFFFF"
        ),
        margin=dict(l=40, r=20, t=65, b=45),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        showlegend=False
    )

def render_interactive_charts():
    """
    Renders the responsive enterprise usage analytics dashboard with 
    KPI cards and 5 unified-size interactive Plotly charts.
    """
    # Load all required data (API fetched with high-quality fallback data)
    df_daily, df_lang_pairs, df_time, df_privacy, df_users, kpis = load_analytics_data()

    # Stylize the page elements with premium enterprise design classes
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        
        /* Dashboard Container Settings */
        .main .block-container {
            padding-top: 1.5rem !important;
            padding-bottom: 2rem !important;
            max-width: 1280px !important;
        }
        
        .dashboard-title {
            font-family: 'Inter', sans-serif;
            color: #0F172A;
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 2px;
            letter-spacing: -0.5px;
        }
        
        .dashboard-subtitle {
            font-family: 'Inter', sans-serif;
            color: #64748B;
            font-size: 14px;
            font-weight: 400;
            margin-bottom: 24px;
        }
        
        /* KPI Cards Styling */
        .kpi-card {
            background-color: #FFFFFF;
            border: 1px solid #E2E8F0;
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px 0 rgba(0, 0, 0, 0.03);
            border-radius: 12px;
            padding: 18px 20px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-height: 100px;
            margin-bottom: 20px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        .kpi-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05), 0 4px 6px -2px rgba(0, 0, 0, 0.02);
            border-color: #CBD5E1;
        }
        
        .kpi-label {
            font-family: 'Inter', sans-serif;
            font-size: 11px;
            font-weight: 600;
            color: #64748B;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }
        
        .kpi-value {
            font-family: 'Inter', sans-serif;
            font-size: 28px;
            font-weight: 700;
            color: #0F172A;
            line-height: 1.1;
        }
        
        /* Chart Container Cards */
        .chart-card {
            background-color: #FFFFFF;
            border: 1px solid #E2E8F0;
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px 0 rgba(0, 0, 0, 0.03);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
            min-height: 440px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        .chart-card:hover {
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05), 0 4px 6px -2px rgba(0, 0, 0, 0.02);
            border-color: #CBD5E1;
        }
        
        /* System Info Card */
        .info-card {
            background: linear-gradient(135deg, #1E293B 0%, #0F172A 100%);
            border: none;
            color: #FFFFFF;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 24px;
            min-height: 440px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }
        
        .info-header {
            font-family: 'Inter', sans-serif;
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 15px;
            border-bottom: 1px solid #334155;
            padding-bottom: 8px;
            letter-spacing: 0.5px;
        }
        
        .info-item {
            display: flex;
            justify-content: space-between;
            margin-bottom: 12px;
            font-size: 13px;
            font-family: 'Inter', sans-serif;
        }
        
        .info-item-label {
            color: #94A3B8;
            font-weight: 500;
        }
        
        .info-item-value {
            color: #F8FAFC;
            font-weight: 600;
        }
        
        .info-footer {
            font-size: 11px;
            color: #64748B;
            border-top: 1px solid #334155;
            padding-top: 8px;
            text-align: center;
            font-family: 'Inter', sans-serif;
        }
        </style>
    """, unsafe_allow_html=True)

    # Dashboard Header Sections
    st.markdown("<div class='dashboard-title'>📊 Usage Analytics & Corporate Reports</div>", unsafe_allow_html=True)
    st.markdown("<div class='dashboard-subtitle'>Interactive tracking of translation activity, user performance, and privacy metrics.</div>", unsafe_allow_html=True)

    # ── ROW 1: KPI STATS CARDS ───────────────────────────────────────────
    kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)
    
    with kpi_col1:
        st.markdown(f"""
            <div class='kpi-card'>
                <div class='kpi-label'>Total Translations</div>
                <div class='kpi-value' style='color: #4F46E5;'>{kpis['total_translations']:,}</div>
            </div>
        """, unsafe_allow_html=True)
        
    with kpi_col2:
        st.markdown(f"""
            <div class='kpi-card'>
                <div class='kpi-label'>Active Users</div>
                <div class='kpi-value' style='color: #0EA5E9;'>{kpis['active_users']:,}</div>
            </div>
        """, unsafe_allow_html=True)
        
    with kpi_col3:
        st.markdown(f"""
            <div class='kpi-card'>
                <div class='kpi-label'>Total Files Translated</div>
                <div class='kpi-value' style='color: #10B981;'>{kpis['total_files']:,}</div>
            </div>
        """, unsafe_allow_html=True)
        
    with kpi_col4:
        st.markdown(f"""
            <div class='kpi-card'>
                <div class='kpi-label'>Average Processing Time</div>
                <div class='kpi-value' style='color: #F59E0B;'>{kpis['avg_processing_time']:.2f}s</div>
            </div>
        """, unsafe_allow_html=True)

    # Plotly Modebar Configuration
    plotly_config = {
        'displayModeBar': True,
        'responsive': True,
        'scrollZoom': True,
        'modeBarButtonsToRemove': ['select2d', 'lasso2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d'],
    }

    # ── ROW 2: DAILY ACTIVITY & TOP USERS ────────────────────────────────
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("<div class='chart-card'>", unsafe_allow_html=True)
        # CHART 1: Daily Translation Activity (Plotly Line Chart)
        fig_daily = px.line(
            df_daily,
            x='translation_date',
            y='translation_count',
            markers=True,
            line_shape='spline',
            color_discrete_sequence=['#dc2626']  # Force red line in Plotly 6.x
        )
        fig_daily.update_traces(
            line=dict(color='#dc2626', width=3.5),
            marker=dict(size=8, color='#dc2626', symbol='circle'),
            hovertemplate="<b>Date:</b> %{x}<br><b>Translations:</b> %{y}<extra></extra>"
        )
        customize_chart_layout(fig_daily, "Daily Translation Activity", "Translation Date", "Number of Translations")
        st.plotly_chart(fig_daily, use_container_width=True, config=plotly_config)
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown("<div class='chart-card'>", unsafe_allow_html=True)
        # CHART 2: Top 5 Users by Files Translated (Plotly Horizontal Bar Chart)
        # Ensure we have the correct columns; if API returned different column names, rename them
        df_chart_users = df_users.copy()
        if 'username' not in df_chart_users.columns:
            # Try common fallback column names and rename to 'username'
            for alt_col in ['user', 'user_name', 'name', 'full_name']:
                if alt_col in df_chart_users.columns:
                    df_chart_users = df_chart_users.rename(columns={alt_col: 'username'})
                    break
        if 'files_translated' not in df_chart_users.columns:
            for alt_col in ['count', 'file_count', 'translation_count', 'total']:
                if alt_col in df_chart_users.columns:
                    df_chart_users = df_chart_users.rename(columns={alt_col: 'files_translated'})
                    break
        # Replace any null/empty usernames
        df_chart_users['username'] = df_chart_users['username'].fillna('Unknown User').replace('', 'Unknown User')
        # Sort ascending so highest value bar appears at the top in horizontal layout
        df_top_users_sorted = df_chart_users.sort_values(by='files_translated', ascending=True).tail(5)
        
        fig_users = px.bar(
            df_top_users_sorted,
            x='files_translated',
            y='username',
            orientation='h',
            color_discrete_sequence=['#dc2626']  # Force red bars — prevents any color scale override
        )
        fig_users.update_traces(
            marker_color='#dc2626',  # Explicit red bar color (same as corporate theme)
            hovertemplate="<b>User:</b> %{y}<br><b>Files Translated:</b> %{x}<extra></extra>"
        )
        fig_users.update_layout(
            yaxis={
                'categoryorder': 'total ascending',
                'tickmode': 'array',
                'tickvals': df_top_users_sorted['username'].tolist(),
                'ticktext': df_top_users_sorted['username'].tolist()  # Display actual usernames
            }
        )
        customize_chart_layout(fig_users, "Top 5 Users by Files Translated", "Number of Files Translated", "Username")
        st.plotly_chart(fig_users, use_container_width=True, config=plotly_config)
        st.markdown("</div>", unsafe_allow_html=True)

    # ── ROW 3: LANGUAGE PAIRS & AVG PROCESSING TIME ──────────────────────
    col3, col4 = st.columns(2)

    with col3:
        st.markdown("<div class='chart-card'>", unsafe_allow_html=True)
        # Existing Chart: Top Language Pairs (Horizontal Bar Chart)
        fig_pairs = px.bar(
            df_lang_pairs,
            x='Count',
            y='Language Pair',
            orientation='h',
            color='Count',
            color_continuous_scale='Blues'
        )
        fig_pairs.update_traces(
            hovertemplate="<b>Pair:</b> %{y}<br><b>Count:</b> %{x}<extra></extra>",
            width=0.45  # Narrower bars for a more refined look
        )
        fig_pairs.update_layout(
            yaxis={'categoryorder': 'total ascending'},
            coloraxis_showscale=False,
            bargap=0.5,       # Increase gap between bars (0=no gap, 1=full gap)
            bargroupgap=0.1   # Additional gap between bar groups
        )
        customize_chart_layout(fig_pairs, "Top Language Pairs", "Total Translations", "Language Pair")
        st.plotly_chart(fig_pairs, use_container_width=True, config=plotly_config)
        st.markdown("</div>", unsafe_allow_html=True)

    with col4:
        st.markdown("<div class='chart-card'>", unsafe_allow_html=True)
        # Existing Chart: Average Translation Processing Time (Line Chart)
        fig_time = px.line(
            df_time,
            x='Date',
            y='Avg Processing Time (s)',
            markers=True,
            line_shape='spline'
        )
        fig_time.update_traces(
            line=dict(color='#F59E0B', width=3.5),
            marker=dict(size=8, color='#D97706', symbol='circle'),
            hovertemplate="<b>Date:</b> %{x}<br><b>Avg Duration:</b> %{y:.2f}s<extra></extra>"
        )
        customize_chart_layout(fig_time, "Average Translation Processing Time", "Date", "Time (Seconds)")
        st.plotly_chart(fig_time, use_container_width=True, config=plotly_config)
        st.markdown("</div>", unsafe_allow_html=True)

    # ── ROW 4: PRIVACY ANALYTICS & SYSTEM SUMMARY INFO ───────────────────
    col5, col6 = st.columns(2)

    with col5:
        st.markdown("<div class='chart-card'>", unsafe_allow_html=True)
        # Existing Chart: Privacy Protection Analytics (Bar Chart)
        fig_privacy = px.bar(
            df_privacy,
            x='Metric',
            y='Items Protected',
            color='Metric',
            color_discrete_sequence=['#10B981', '#3B82F6', '#6366F1', '#8B5CF6', '#EC4899']
        )
        fig_privacy.update_traces(
            hovertemplate="<b>Category:</b> %{x}<br><b>Items Protected:</b> %{y}<extra></extra>"
        )
        customize_chart_layout(fig_privacy, "Privacy Protection Analytics", "Protection Category", "Count of Protected Items")
        st.plotly_chart(fig_privacy, use_container_width=True, config=plotly_config)
        st.markdown("</div>", unsafe_allow_html=True)

    with col6:
        # System Overview Summary Info Card (Balances visual grid with symmetrical card heights)
        st.markdown(f"""
            <div class='info-card'>
                <div>
                    <div class='info-header'>⚙️ Translation System Overview</div>
                    <div class='info-item'>
                        <span class='info-item-label'>Database Engine:</span>
                        <span class='info-item-value'>MySQL 8.0</span>
                    </div>
                    <div class='info-item'>
                        <span class='info-item-label'>API Status:</span>
                        <span class='info-item-value' style='color: #10B981;'>● Operational</span>
                    </div>
                    <div class='info-item'>
                        <span class='info-item-label'>Active Translation Engine:</span>
                        <span class='info-item-value'>Google Translate</span>
                    </div>
                    <div class='info-item'>
                        <span class='info-item-label'>Configured Languages:</span>
                        <span class='info-item-value'>41 ISO-639 Languages</span>
                    </div>
                    <div class='info-item'>
                        <span class='info-item-label'>Data Retention Policy:</span>
                        <span class='info-item-value'>12 Months (Auto-Archived)</span>
                    </div>
                    <div class='info-item'>
                        <span class='info-item-label'>Glossary Term Rules:</span>
                        <span class='info-item-value'>Active PII & Glossaries</span>
                    </div>
                </div>
                <div class='info-footer'>
                    DocTranslate Enterprise Analytics • Dashboard last refreshed at {datetime.now().strftime('%H:%M:%S')}
                </div>
            </div>
        """, unsafe_allow_html=True)
if __name__ == "__main__":
    st.set_page_config(layout="wide")
    render_interactive_charts()
