import base64
import os

from PIL import Image

import pytesseract

from urllib import parse
from retry import retry
from pytesseract import image_to_string
from dotenv import load_dotenv
from tqdm.auto import tqdm

load_dotenv()

URL_LOGIN = 'https://www.citapreviadnie.es/citaPreviaDniExp/InicioDNINIE.action'
URL_DNI = 'https://www.citapreviadnie.es/citaPreviaDniExp/InicioTramite.action?idDocumento=D'
URL_PASAPORTE = 'https://www.citapreviadnie.es/citaPreviaDniExp/InicioTramite.action?idDocumento=P'

def extraer_datos_unidad(unidad, provincia):
    """Extraer propiedades de una unidad (nombre, url, etc)"""
    link = unidad.find_element_by_tag_name('a')
    notas = unidad.find_elements_by_class_name('notas')

    datos_unidad = {
        'url': link.get_attribute('href'),
        'name': link.text,
        'title': link.get_attribute('title'),
        'direccion': notas[0].text,
        'horario': notas[1].text,
        'disponibilidad': notas[2].text,
        'province': provincia,
    }
    
    return datos_unidad


def parse_urlargs(url):
    """Extract parameters from a URL.
    
    Taken from: https://stackoverflow.com/questions/5074803/retrieving-parameters-from-a-url"""
    query = parse.parse_qs(parse.urlparse(url).query)
    return {k:v[0] if v and len(v) == 1 else v for k,v in query.items()}


def get_remaining_months(driver):
    """Returns a dictionary with links for the remaining months to scrape"""
    link_elements = driver.find_elements_by_tag_name('a')
    months = {}

    for link_elem in link_elements:
        url = link_elem.get_attribute('href')
        if url and 'numMes' in url:
            months[link_elem.text] = url
    
    return months


def get_remaining_days(driver):
    """Returns a dictionary with links for the remaining days to scrape in the current month"""
    link_elements = driver.find_elements_by_tag_name('a')
    days = {}
    for link_elem in link_elements:
        url = link_elem.get_attribute('href')
        if url and 'numDia' in url:
            days[link_elem.get_attribute('title')] = url
            
    return days


def extract_hours(driver, province, unidad):
    """Returns a list of dictionaries with info for each available slot"""
    horas = driver.find_elements_by_class_name('hora')
    
    horas_params = []
    for hora in horas:
        hora_disponible = hora.find_element_by_tag_name('a')
        hora_url = hora_disponible.get_attribute('href')
        hora_title = hora_disponible.get_attribute('title')

        citas = hora_title.split(' ')[1]
        citas = int(citas.strip('()'))
        
        hora_params = {
            **parse_urlargs(hora_url),
            'Citas': citas,
            'Provincia': province,
            'Unidad': unidad,
        }

        horas_params.append(hora_params)
    
    return horas_params


def extract_unidad(driver, province, unidad):
    """Extract all slots for a police unit
    
    Note: current month and day URLs are not selectable
    """
    unidad_name = unidad['name']
    driver.get(unidad['url'])
    
    # Get all slots for today
    citas = extract_hours(driver, province, unidad_name)

    # Get all slots for next days in the same month
    month_days = get_remaining_days(driver)
    for date, date_url in tqdm(month_days.items(), desc='Current month'):
        driver.get(date_url)
        citas.extend(extract_hours(driver, province, unidad_name))

    # Get all slots for all days in the next months
    next_months = get_remaining_months(driver)
    for month, month_url in next_months.items():
        driver.get(month_url)
        month_days = get_remaining_days(driver)
        for _, date_url in tqdm(month_days.items(), desc=month):
            driver.get(date_url)
            citas.extend(extract_hours(driver, province, unidad_name))
    
    return citas


def download_captcha_img(driver, save_name='captcha.jpg', class_name='borderCapcha'):
    # Download captcha as image
    # From https://stackoverflow.com/questions/36636255/selenium-downloading-different-captcha-image-than-the-one-in-browser
    img_base64 = driver.execute_script("""
        var ele = arguments[0];
        var cnv = document.createElement('canvas');
        cnv.width = ele.width; cnv.height = ele.height; 
        cnv.getContext('2d').drawImage(ele, 0, 0);
        return cnv.toDataURL('image/jpeg').substring(22);    
        """, driver.find_element_by_class_name(class_name))

    with open(save_name, 'wb') as f:
        f.write(base64.b64decode(img_base64))


@retry(exceptions=ValueError, tries=10)
def login(driver, tesseract_check=False, tesseract_path="C:\\Program Files\\Tesseract-OCR\\tesseract.exe"):
    
    driver.get(URL_LOGIN)
    
    download_captcha_img(driver)
    
    if tesseract_check: 
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
    
    captcha_img = Image.open('captcha.jpg')
    solved_captcha = image_to_string(captcha_img).replace(' ', '')[:4].upper()
    
    NUM_DOCUMENTO = os.environ['NUM_DOCUMENTO']
    LETRA_DOCUMENTO = os.environ['LETRA_DOCUMENTO']
    CODIGO_EQUIPO = os.environ['CODIGO_EQUIPO']
    FECHA_VALIDEZ = os.environ['FECHA_VALIDEZ']

    inputs = {
        'numDocumento': NUM_DOCUMENTO,
        'letraDocumento': LETRA_DOCUMENTO,
        'codEquipo': CODIGO_EQUIPO,
        'fechaValidez': FECHA_VALIDEZ,
        'codSeguridad': solved_captcha,
    }
    
    for k,v in inputs.items():
        elem = driver.find_element_by_id(k)
        elem.send_keys(v)

    submit_button = driver.find_element_by_xpath('/html/body/div/div[4]/div[1]/form/p[6]/input[1]')
    submit_button.click()

    errors = driver.find_elements_by_class_name('pError')
        
    if errors:    
        raise ValueError('CapthaError')