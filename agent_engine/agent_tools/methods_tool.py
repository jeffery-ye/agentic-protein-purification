import xml.etree.ElementTree as ET
import re

class MethodsTool:
    def parse_article(self, xml_article):
        tree = ET.ElementTree(ET.fromstring(xml_article))
        root = tree.getroot()

        pmcid_element = root.find(".//article-id[@pub-id-type='pmcid']")
        pmcid = pmcid_element.text if pmcid_element is not None else 'Not Found'

        title_element = root.find('.//article-title')
        if title_element is not None:
            article_title = "".join(title_element.itertext()).strip() 
            article_title = re.sub(r'\s+', ' ', article_title).strip()
        else:
            article_title = "Untitled"

        # Extract abstract
        abstract_text = 'N/A'
        abstract = root.find('.//abstract')

        if abstract is not None:
            for element in abstract:
                if element.tag == 'p': 
                    p_text = "".join(element.itertext()).strip()
                    p_text = re.sub(r'\s+', ' ', p_text).strip()
                    if p_text:
                        abstract_text = p_text

        # Extract body text
        text_segments = []
        body = root.find('.//body')

        if body is not None:
            for element in body:
                if element.tag == 'sec':
                    sec_title_text = None
                    title_tag = element.find('./title')
                    if title_tag is not None:
                        raw_title = "".join(title_tag.itertext()).strip()
                        cleaned_title = re.sub(r'\s+', ' ', raw_title).strip()
                        if cleaned_title:
                                sec_title_text = cleaned_title

                    if sec_title_text:
                        text_segments.append(f"## {sec_title_text} ##")

                    for p_tag in element.findall('.//p'):
                        p_text = "".join(p_tag.itertext()).strip()
                        p_text = re.sub(r'\s+', ' ', p_text).strip()
                        if p_text:
                            text_segments.append(p_text)

                elif element.tag == 'p': 
                    p_text = "".join(element.itertext()).strip()
                    p_text = re.sub(r'\s+', ' ', p_text).strip()
                    if p_text:
                        text_segments.append(p_text)

        if text_segments:
            full_text = "\n".join(segment for segment in text_segments if segment)
        else:
            full_text = 'N/A'
            
        # Find Materials and methods
        pattern = r"##\s*(.*?)\s*##(.*?)(?=(?:##|$))"
        methods_sections = []
        
        for match in re.finditer(pattern, full_text, re.DOTALL):
            title = match.group(1).strip()
            title_lower = title.lower()
            if any(kw in title_lower for kw in ("method", "experimental", "protocol", "purification", "expression")):
                methods_sections.append(match.group(2).strip())

        methods = "\n\n".join(methods_sections) if methods_sections else None

        return {
            'pmcid': pmcid,
            'title': article_title,
            'abstract': abstract_text,
            'full_text': full_text,
            'methods': methods
        }
