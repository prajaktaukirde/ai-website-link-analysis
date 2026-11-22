from typing import Literal

from pydantic import BaseModel, ConfigDict, HttpUrl, TypeAdapter, field_validator

URL = TypeAdapter(HttpUrl)


def website_url(value: str) -> HttpUrl:
    url = URL.validate_python(value)
    if url.username is not None or url.password is not None:
        raise ValueError("URLs cannot include credentials")
    return url


class LinkRequest(BaseModel):
    url: HttpUrl

    @field_validator("url", mode="before")
    @classmethod
    def check_url(cls, value):
        return website_url(value)


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Brand(ModelOutput):
    name: str
    category: str
    summary: str


class Competitor(ModelOutput):
    name: str
    comparison: str
    strengths: list[str]
    gaps: list[str]
    basis: Literal["mentioned_on_submitted_site", "ai_generated_suggestion"]


class Competition(ModelOutput):
    brand_overview: str
    main_products_and_services: list[str]
    competitors: list[Competitor]
    strengths: list[str]
    opportunities: list[str]


Platform = Literal["Google", "Facebook", "Instagram", "LinkedIn", "X"]


class SearchQuery(ModelOutput):
    query: str
    platforms: list[Platform]
    intent: str
    business_relevance: str
    target_audience: str


class SEO(ModelOutput):
    primary_keyword: str
    secondary_keywords: list[str]
    long_tail_keywords: list[str]
    business_keywords: list[str]
    recommended_title: str
    title_structure: str
    meta_description: str
    supporting_content: list[str]


class Service(ModelOutput):
    name: str
    description: str
    target_industry: str
    target_customer: str
    main_benefit: str
    basis: Literal["website_content", "inferred_recommendation"]
    evidence: str


class Audience(ModelOutput):
    primary_audience: list[str]
    secondary_audience: list[str]
    industries: list[str]
    business_types: list[str]
    customer_profiles: list[str]
    geography: list[str]
    customer_needs: list[str]
    customer_interests: list[str]
    marketing_segments: list[str]


class Analysis(ModelOutput):
    competitor_analysis: Competition
    target_search_queries: list[SearchQuery]
    seo_improvements: SEO
    top_services: list[Service]
    target_audience: Audience

    @field_validator("target_search_queries")
    @classmethod
    def check_queries(cls, queries: list[SearchQuery]) -> list[SearchQuery]:
        distinct_queries = {query.query.strip().casefold() for query in queries}
        if len(queries) != 5 or len(distinct_queries) != 5:
            raise ValueError("Expected five distinct queries")
        covered_platforms = {platform for query in queries for platform in query.platforms}
        if any(not query.platforms for query in queries) or covered_platforms != {
            "Google", "Facebook", "Instagram", "LinkedIn", "X"
        }:
            raise ValueError("Queries must collectively cover all five platforms")
        return queries

    @field_validator("top_services")
    @classmethod
    def check_services(cls, services: list[Service]) -> list[Service]:
        distinct_services = {service.name.strip().casefold() for service in services}
        if len(services) != 5 or len(distinct_services) != 5:
            raise ValueError("Expected five distinct service entries")
        return services


class Report(ModelOutput):
    brand: Brand
    analysis: Analysis


class LinkResponse(Report):
    success: Literal[True] = True
    website: str
    sources: list[str]
