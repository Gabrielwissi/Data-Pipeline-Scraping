import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.action_chains import ActionChains
import time
import re

# =============================================================
# CONFIGURAÇÕES
# =============================================================

EXCECOES_NOMES = {
    "The Grand Padre Adelino": "the-grand-padre-adeino",
}

HOST        = "https://adb-7405605511067219.19.azuredatabricks.net/"
PROFILE     = "ga76@hotmail.com"
CLUSTER_ID  = "0319-141604-l9qeqvmq"
CAMINHO_CSV = "/Volumes/gabriel/default/arquivos/data_gold.csv"


# =============================================================
# FUNÇÕES AUXILIARES
# =============================================================

def formatar_url(texto):
    if pd.isna(texto): return ""
    if texto in EXCECOES_NOMES: return EXCECOES_NOMES[texto]
    txt = texto.lower().strip()
    subs = {'á':'a','à':'a','â':'a','ã':'a','é':'e','ê':'e',
            'í':'i','ó':'o','ô':'o','õ':'o','ú':'u','ç':'c'}
    for k, v in subs.items(): txt = txt.replace(k, v)
    return re.sub(r'[^\w\s-]', '', txt).replace(" ", "-")


def extrair_nums_title(title):
    title_lower = title.lower()
    match_dorm  = re.search(r'(\d+)\s*dorm',      title_lower)
    match_suite = re.search(r'(\d+)\s*su[íi]te',  title_lower)
    match_m2    = re.search(r'(\d+)\s*m²',         title_lower)
    metro = match_m2.group(1)    if match_m2    else None
    dorm  = match_dorm.group(1)  if match_dorm  else (match_suite.group(1) if match_suite else None)
    if metro and dorm:
        return sorted([metro, dorm], reverse=True)
    return []


def extrair_imagem_carrossel(driver, link, idx):
    """Sobe pelo DOM para encontrar o slick-slider pai do link clicado,
    depois busca o slide ativo (slick-current) para evitar pegar imagens
    de outros carrosséis na página."""
    carousel = driver.execute_script("""
        var link = arguments[0];
        var el = link;
        while (el) {
            if (el.classList && el.classList.contains('slick-slider')) return el;
            el = el.parentElement;
        }
        return null;
    """, link)

    if carousel:
        candidatos = carousel.find_elements(
            By.CSS_SELECTOR,
            "div.slick-current img:not([class*='icon'])"
        )
        return next(
            (img.get_attribute("data-src") or img.get_attribute("src")
             for img in candidatos
             if (img.get_attribute("data-src") or img.get_attribute("src") or "")
             and not any(excluir in (img.get_attribute("data-src") or img.get_attribute("src") or "")
             for excluir in [".svg", "/icons/", "play.png"])),
            None
        )
    return None


def realizar_retry_plantas(driver, b_fmt, n_fmt, m_alvo, d_alvo):
    canais  = ["studios", "dialogo-offices", "dialogo-mall"]
    sufixos = ["-smart", "", "-offices", "-mall", "-studios"]
    for canal in canais:
        for suf in sufixos:
            url_tentativa = f"https://www.dialogo.com.br/imoveis/{b_fmt}/{canal}/{n_fmt}{suf}"
            try:
                driver.get(url_tentativa)
                if "Não encontrado" in driver.title: continue
                links = driver.find_elements(By.CSS_SELECTOR, "a.gallery__link.plantas")
                for link in links:
                    nums_site = extrair_nums_title(link.get_attribute("title"))
                    nums_alvo = sorted([str(m_alvo), str(d_alvo)], reverse=True)
                    if nums_site and nums_site == nums_alvo:
                        driver.execute_script("arguments[0].scrollIntoView();", link)
                        ActionChains(driver).move_to_element(link).click().perform()
                        idx = link.get_attribute("data-slide")
                        try:
                            WebDriverWait(driver, 5).until(
                                lambda d: d.find_element(By.CSS_SELECTOR, "div.slick-current")
                                           .get_attribute("data-slick-index") == idx
                            )
                        except:
                            time.sleep(2)
                        url_img = extrair_imagem_carrossel(driver, link, idx)
                        if url_img:
                            return url_img, link.get_attribute("title"), url_tentativa
            except:
                continue
    return None, "Não encontrado", "Falha total"


def configurar_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--start-maximized")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(30)
    return driver


# =============================================================
# EXECUÇÃO PRINCIPAL
# =============================================================

def main():
    print("🚀 Iniciando Crawler JD House no Databricks...")

    # --- Conexão Spark ---
    # Se rodar dentro do Databricks (via Git), spark já existe nativamente.
    # Se rodar local no VS Code, conecta via DatabricksSession.
    spark_session = None
    try:
        spark_session = spark
        print("✅ Spark nativo detectado (rodando dentro do Databricks).")
    except NameError:
        try:
            from databricks.connect import DatabricksSession
            spark_session = DatabricksSession.builder \
                .remote(host=HOST, cluster_id=CLUSTER_ID) \
                .profile(PROFILE) \
                .getOrCreate()
            print("✅ Conectado remotamente via VS Code.")
        except Exception as e:
            print(f"❌ Erro de conexão Spark: {e}")
            return

    if spark_session is None:
        print("❌ Spark não disponível.")
        return
    # --- Leitura do CSV ---
    try:
        df_spark = spark_session.read.csv(CAMINHO_CSV, header=True, sep=";", encoding="utf-8")
        df_original = df_spark.toPandas()
        
        # 1. Remove linhas onde 'Empreendimento' é igual ao nome da própria coluna (cabeçalho repetido)
        df_original = df_original[df_original['Empreendimento'].str.lower() != 'empreendimento']
        
        # 2. Remove linhas onde a Metragem ou Dormitórios estão vazios (os 'NaN' que geram o erro)
        df_original = df_original.dropna(subset=['Metragem_Base', 'Dorms'])
        
        # 3. Filtro extra: Garante que Metragem_Base seja um número (evita palavras como 'Previsão')
        df_original = df_original[df_original['Metragem_Base'].astype(str).str.contains(r'\d')]
        
        print(f"📊 CSV limpo e carregado: {len(df_original)} linhas reais de apartamentos.")
    except Exception as e:
        print(f"❌ Erro ao ler/limpar CSV: {e}")
        return
        

    df_predios_unicos = df_original[['Empreendimento', 'Bairro']].drop_duplicates()
    driver = configurar_driver()

    lista_dim_predios  = []
    lista_fact_plantas = []

    try:
        # -------------------------------------------------------
        # LOOP PRINCIPAL — um prédio por vez
        # -------------------------------------------------------
        for _, row_p in df_predios_unicos.iterrows():
            nome_emp = row_p['Empreendimento']
            b_fmt    = formatar_url(row_p['Bairro'])
            n_fmt    = formatar_url(nome_emp)

            url_principal = f"https://www.dialogo.com.br/imoveis/{b_fmt}/apartamentos/{n_fmt}"
            print(f"\n🏢 Prédio: {nome_emp} | URL: {url_principal}")
            driver.get(url_principal)

            # --- A. COLETA DADOS GERAIS (DIM_PREDIOS) ---
            dados_p = {
                'Empreendimento': nome_emp, 'Bairro': row_p['Bairro'],
                'Descricao': "N/A", 'Endereco': "N/A", 'Desc_Bairro': "N/A",
                'URL_Fachada': None, 'URL_Book': None
            }
            try:
                WebDriverWait(driver, 7).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".about-content p"))
                )
                dados_p['Descricao']  = driver.find_element(By.CSS_SELECTOR, ".about-content p").text.strip()
                dados_p['Endereco']   = driver.find_element(By.CSS_SELECTOR, ".pb-3 span.localizacao__text").text.strip()

                ps_bairro = driver.find_elements(By.CSS_SELECTOR, ".bairro__text p")
                dados_p['Desc_Bairro'] = next(
                    (p.text.strip() for p in ps_bairro if len(p.text.strip()) > 50), "N/A"
                )
                img_f = driver.find_element(
                    By.CSS_SELECTOR, "img[class*='banner'], .w-slider-mask img, .main-image img"
                )
                dados_p['URL_Fachada'] = img_f.get_attribute("data-src") or img_f.get_attribute("src")

                book = driver.find_element(By.XPATH, "//a[contains(@href, '.pdf')]")
                dados_p['URL_Book'] = book.get_attribute("href")
                print("  ✅ Dados gerais capturados.")
            except:
                print("  ⚠️ Falha em algum campo da Dim_Predios.")

            lista_dim_predios.append(dados_p)

            # --- B. PRIMEIRA PASSAGEM: PLANTAS ---
            tipologias = df_original[df_original['Empreendimento'] == nome_emp]
            for _, row_t in tipologias.iterrows():
                m_alvo    = str(row_t['Metragem_Base']).strip()
                d_alvo    = str(row_t['Dorms']).strip()
                url_img   = None
                nome_site = "Não encontrado"

                print(f"  🔍 Buscando: {m_alvo}m² | {d_alvo} Dorms")

                try:
                    links = driver.find_elements(By.CSS_SELECTOR, "a.gallery__link.plantas")
                    print(f"    📋 Total de links encontrados: {len(links)}")

                    for link in links:
                        t_site    = link.get_attribute("title")
                        nums_site = extrair_nums_title(t_site)
                        nums_alvo = sorted([m_alvo, d_alvo], reverse=True)
                        print(f"    🔗 title: '{t_site}' | nums: {nums_site} | alvo: {nums_alvo}")

                        if nums_site and nums_site == nums_alvo:
                            nome_site = t_site.strip()
                            driver.execute_script("arguments[0].scrollIntoView();", link)
                            ActionChains(driver).move_to_element(link).click().perform()
                            idx = link.get_attribute("data-slide")

                            print(f"    🔎 data-slide do link: {idx}")
                            try:
                                WebDriverWait(driver, 5).until(
                                    lambda d: d.find_element(By.CSS_SELECTOR, "div.slick-current")
                                               .get_attribute("data-slick-index") == idx
                                )
                                print(f"    🔎 slick-current confirmado no índice {idx}")
                            except:
                                atual = driver.find_element(By.CSS_SELECTOR, "div.slick-current")
                                print(f"    ⚠️ Timeout! slick-current está em: {atual.get_attribute('data-slick-index')}")
                                time.sleep(1.5)

                            url_img = extrair_imagem_carrossel(driver, link, idx)
                            print(f"    ✅ Match: {nome_site} | URL: {url_img}")
                            break

                    if not url_img:
                        print("    ❌ Sem match nesta página.")

                except Exception as e:
                    print(f"    💥 Erro: {e}")

                lista_fact_plantas.append({
                    'Empreendimento': nome_emp,
                    'Bairro':         row_p['Bairro'],
                    'Metragem':       m_alvo,
                    'Dorms':          d_alvo,
                    'Nome_Site':      nome_site,
                    'URL_Planta':     url_img,
                    'Status':         'Pág. Principal'
                })

        # -------------------------------------------------------
        # SEGUNDA PASSAGEM: RETRY para itens sem planta
        # -------------------------------------------------------
        print("\n🔄 Iniciando Retry para itens faltantes...")
        for item in lista_fact_plantas:
            if not item['URL_Planta']:
                print(f"  ⚡ Retry: {item['Empreendimento']} ({item['Metragem']}m²)")
                nova_url, novo_nome, _ = realizar_retry_plantas(
                    driver,
                    formatar_url(item['Bairro']),
                    formatar_url(item['Empreendimento']),
                    item['Metragem'],
                    item['Dorms']
                )
                if nova_url:
                    item['URL_Planta'] = nova_url
                    item['Nome_Site']  = novo_nome
                    item['Status']     = 'Recuperado Retry'

        # -------------------------------------------------------
        # RESUMO
        # -------------------------------------------------------
        df_resumo = pd.DataFrame(lista_fact_plantas)
        print("\n" + "="*40)
        print("📊 RESUMO FINAL")
        print("="*40)
        print(f"✅ Sucesso Direto : {len(df_resumo[df_resumo['Status'] == 'Pág. Principal'])}")
        print(f"🔄 Recuperados   : {len(df_resumo[df_resumo['Status'] == 'Recuperado Retry'])}")
        print(f"❌ Falhas        : {df_resumo['URL_Planta'].isna().sum()}")

        # -------------------------------------------------------
        # SALVAMENTO NO UNITY CATALOG
        # -------------------------------------------------------
        print("\n📥 Gravando tabelas no catálogo...")

        df_dim  = pd.DataFrame(lista_dim_predios)
        spark_session.createDataFrame(df_dim).write.mode("overwrite") \
             .saveAsTable("gabriel.default.dim_predios_scraping")

        spark_session.createDataFrame(df_resumo).write.mode("overwrite") \
             .saveAsTable("gabriel.default.fact_plantas_scraping")

        print("✅ Pipeline finalizado com sucesso!")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
