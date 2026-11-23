"""전용 문서의 작성 틀. 기획서(인터뷰 반영 v2, 2026-11-23)의 항목을 그대로 옮겼다.

문서는 만들 때의 버전을 고정한다. 틀을 고칠 때는 기존 버전을 지우지 말고 새 버전을 더한다.
특정 펀딩 플랫폼이나 지원기관의 공식 제출 양식을 보증하지 않는 공통 작성 틀이다.
"""
from dataclasses import dataclass

from app.errors import AppError


@dataclass(frozen=True)
class Section:
    section_id: str
    title: str
    question: str
    guide: str
    required: bool


@dataclass(frozen=True)
class Template:
    template_id: str
    version: int
    name: str
    sections: tuple[Section, ...]

    def section(self, section_id: str) -> Section | None:
        return next((section for section in self.sections if section.section_id == section_id), None)


FUNDING_V1 = Template("funding", 1, "펀딩 상세페이지", (
    Section("F01", "작품·프로젝트 소개", "무엇을 만드는지, 핵심 메시지는 무엇인가요?",
            "기존 상품기획을 바탕으로 소개 초안과 문장 수정을 돕는다.", True),
    Section("F02", "고객과 구매 가치", "누가 어떤 상황에서 사용하고, 어떤 점이 좋은가요?",
            "고객·구매 가치 기록을 설명으로 연결한다.", True),
    Section("F03", "제작 배경·브랜드 이야기", "왜 만들었는지, 창작자의 작업 방식과 브랜드 방향은 무엇인가요?",
            "실제 기록에 있는 이야기만 정리한다.", False),
    Section("F04", "특징·구성·사용 장면", "소재·크기·구성·사용법·사진 설명을 적어 주세요.",
            "입력한 사실로 특장점을 정리하며 확인되지 않은 수치·효능을 만들지 않는다.", True),
    Section("F05", "리워드·가격·수량", "어떤 구성으로 제공하나요? 금액·수량 중 확정한 것과 아직 확인할 것은 무엇인가요?",
            "기존 확정 가격을 참고하며 문서 수정으로 판매가를 확정하지 않는다.", True),
    Section("F06", "제작·배송 계획", "제작 단계, 확인할 제작 조건, 예상 일정은 무엇인가요?",
            "확정되지 않은 날짜는 확인 과제로 남기고 사용자가 확인한 뒤 일정에 연결한다.", True),
    Section("F07", "사용·관리·이용 안내", "실제 관리 방법, 안내, 정책을 적어 주세요.",
            "교환 조건·판매 정책을 AI가 임의로 확정하지 않는다.", False),
    Section("F08", "위험 요인·남은 확인", "바뀔 수 있는 조건과 아직 확인하지 않은 것은 무엇인가요?",
            "필요한 확인 행동을 할 일 후보로 정리한다.", True),
))

BUSINESS_PLAN_V1 = Template("business_plan", 1, "사업계획서", (
    Section("B01", "사업 개요·목표", "어떤 활동을 왜 추진하고, 이번 목표는 무엇인가요?",
            "브랜드·프로젝트 기록으로 요약한다.", True),
    Section("B02", "고객·문제·필요", "고객과 해결할 문제, 실제로 확인한 경험은 무엇인가요?",
            "관찰과 추정을 구분해서 쓴다.", True),
    Section("B03", "상품·서비스와 차별점", "제공할 구성, 핵심 가치, 제작 방식은 무엇인가요?",
            "기존 기획의 연결 내용을 정리한다.", True),
    Section("B04", "시장·경쟁·근거", "직접 조사한 자료, 경쟁 사례, 출처는 무엇인가요?",
            "통계·시장 규모·출처를 AI가 만들어 채우지 않는다.", True),
    Section("B05", "수익 구조·비용·가격", "판매 방식, 원가와 예상 수입·지출의 가정은 무엇인가요?",
            "기존 계산과 실제 기록을 구분해 참고한다.", True),
    Section("B06", "홍보·판매 실행 계획", "어떤 고객에게 어떤 경로로 알리고 판매하나요?",
            "확인할 채널과 후속 작업을 정리한다.", True),
    Section("B07", "운영 역량·자원·추진 일정", "직접 할 일, 협력할 곳, 필요한 자원, 단계별 일정은 무엇인가요?",
            "실제 역량과 확보 여부를 구분하고 할 일로 연결한다.", True),
    Section("B08", "성과 목표·위험·검증 계획", "어떤 결과를 확인하고, 불확실한 부분은 어떻게 확인하나요?",
            "사업 성과를 문서 작성률로 대체하지 않는다.", True),
))

TEMPLATES: dict[tuple[str, int], Template] = {
    (template.template_id, template.version): template for template in (FUNDING_V1, BUSINESS_PLAN_V1)
}
LATEST: dict[str, Template] = {template.template_id: template for template in TEMPLATES.values()}


def latest(template_id: str) -> Template:
    template = LATEST.get(template_id)
    if template is None:
        raise AppError(422, "UNKNOWN_TEMPLATE", "펀딩 상세페이지(funding) 또는 사업계획서(business_plan)만 만들 수 있습니다.")
    return template


def get(template_id: str, version: int) -> Template:
    return TEMPLATES[(template_id, version)]


def to_dict(template: Template) -> dict:
    return {
        "template_id": template.template_id,
        "version": template.version,
        "name": template.name,
        "sections": [
            {"section_id": s.section_id, "title": s.title, "question": s.question, "guide": s.guide, "required": s.required}
            for s in template.sections
        ],
    }
