"""Bounded generalization for the synthetic prototype's external search."""

# Values are fixed public concepts. The model cannot add names, numbers, or
# source text to a generalized query. Extend only with review and regression tests.
GENERAL_CONCEPTS = {
    'tumbler': '다회용 텀블러',
    'mug': '머그컵',
    'beverage_container': '다회용 음료 용기',
    'fashion': '의류 패션',
    'cosmetics': '화장품',
    'food': '식품',
    'stationery': '문구',
    'accessories': '패션 소품',
    'craft': '수공예품',
    'market': '시장 동향',
    'customers': '소비자 수요',
    'price': '가격 비교',
    'product_examples': '상품 사례',
    'crowdfunding': '크라우드펀딩 사례',
    'production': '제작 방법',
    'packaging': '포장',
    'sustainability': '친환경 지속가능성',
    'regulations': '제품 관련 규정',
    'software': '소프트웨어',
    'artificial_intelligence': '인공지능',
    'web_search': '웹 검색',
    'documentation': '개발 문서',
}


def generalized_query(concepts):
    if not isinstance(concepts, list) or not 1 <= len(concepts) <= 4:
        raise ValueError('SEARCH_INPUT_DENIED')
    if any(not isinstance(c, str) or c not in GENERAL_CONCEPTS for c in concepts):
        raise ValueError('SEARCH_INPUT_DENIED')
    if len(set(concepts)) != len(concepts):
        raise ValueError('SEARCH_INPUT_DENIED')
    return ' '.join(value for key, value in GENERAL_CONCEPTS.items() if key in concepts)
