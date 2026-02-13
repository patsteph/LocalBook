"""Citation Verification Service

v1.1.0: Implements CaRR (Citation-aware Rubric Rewards) for RAG responses.
Verifies that claims in answers are supported by cited sources.
Penalizes hallucination and rewards evidence-grounded reasoning.

Based on: "CaRR: Citation-aware Rubric Rewards" (THUDM research)
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict
from enum import Enum


class ClaimSupport(str, Enum):
    """How well a claim is supported by citations."""
    FULLY_SUPPORTED = "fully_supported"  # Claim directly stated in source
    PARTIALLY_SUPPORTED = "partially_supported"  # Some evidence, not complete
    UNSUPPORTED = "unsupported"  # No evidence in cited sources
    NO_CITATION = "no_citation"  # Claim made without any citation


@dataclass
class Claim:
    """A factual claim extracted from an answer."""
    text: str
    citation_refs: List[int] = field(default_factory=list)  # [1], [2], etc.
    support_level: ClaimSupport = ClaimSupport.NO_CITATION
    evidence_snippets: List[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class VerificationResult:
    """Result of verifying an answer against its citations."""
    overall_score: float  # 0.0 - 1.0
    claims: List[Claim] = field(default_factory=list)
    fully_supported_count: int = 0
    partially_supported_count: int = 0
    unsupported_count: int = 0
    no_citation_count: int = 0
    hallucination_risk: str = "low"  # low, medium, high
    feedback: str = ""


class CitationVerifier:
    """Verifies RAG answers against cited sources using CaRR principles."""
    
    # Patterns indicating factual claims that need citation support
    CLAIM_INDICATORS = [
        r'\d+(?:\.\d+)?%',  # Percentages
        r'\$[\d,]+(?:\.\d+)?',  # Dollar amounts
        r'\d{4}',  # Years
        r'(?:increased|decreased|grew|fell|rose|dropped)\s+(?:by|to)',  # Trends
        r'(?:according to|based on|shows that|indicates that)',  # Attribution
        r'(?:first|second|third|largest|smallest|highest|lowest)',  # Superlatives
        r'(?:always|never|every|all|none)',  # Absolute claims
    ]
    
    def __init__(self):
        self._claim_pattern = re.compile('|'.join(self.CLAIM_INDICATORS), re.IGNORECASE)
    
    def extract_claims(self, answer: str) -> List[Claim]:
        """Extract factual claims from an answer.
        
        Identifies sentences that contain factual assertions needing verification.
        """
        claims = []
        
        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', answer)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence or len(sentence) < 20:
                continue
            
            # Check if sentence contains claim indicators
            has_claim_indicator = bool(self._claim_pattern.search(sentence))
            
            # Extract citation references like [1], [2], etc.
            citation_refs = [int(m) for m in re.findall(r'\[(\d+)\]', sentence)]
            
            # Sentences with numbers, statistics, or specific facts are claims
            has_numbers = bool(re.search(r'\d', sentence))
            has_specific_facts = any(word in sentence.lower() for word in [
                'said', 'stated', 'reported', 'announced', 'revealed',
                'found', 'discovered', 'showed', 'demonstrated'
            ])
            
            if has_claim_indicator or has_numbers or has_specific_facts:
                claims.append(Claim(
                    text=sentence,
                    citation_refs=citation_refs,
                    support_level=ClaimSupport.NO_CITATION if not citation_refs else ClaimSupport.UNSUPPORTED
                ))
        
        return claims
    
    def verify_claim(self, claim: Claim, citations: List[Dict]) -> Claim:
        """Verify a single claim against its cited sources.
        
        Args:
            claim: The claim to verify
            citations: List of citation dicts with 'number', 'text', 'snippet' fields
        
        Returns:
            Updated claim with support level and evidence
        """
        if not claim.citation_refs:
            claim.support_level = ClaimSupport.NO_CITATION
            claim.confidence = 0.0
            return claim
        
        # Get relevant citations
        relevant_citations = [
            c for c in citations 
            if c.get('number') in claim.citation_refs
        ]
        
        if not relevant_citations:
            claim.support_level = ClaimSupport.UNSUPPORTED
            claim.confidence = 0.0
            return claim
        
        # Check if claim content appears in cited sources
        claim.text.lower()
        
        # Extract key terms from claim (numbers, names, specific phrases)
        key_terms = self._extract_key_terms(claim.text)
        
        evidence_found = []
        terms_matched = 0
        
        for citation in relevant_citations:
            source_text = (citation.get('text', '') + ' ' + citation.get('snippet', '')).lower()
            
            for term in key_terms:
                if term.lower() in source_text:
                    terms_matched += 1
                    # Find the context around the matched term
                    idx = source_text.find(term.lower())
                    start = max(0, idx - 50)
                    end = min(len(source_text), idx + len(term) + 50)
                    evidence_found.append(source_text[start:end].strip())
        
        # Calculate support level based on term matches
        if not key_terms:
            match_ratio = 0.5  # No specific terms to verify
        else:
            match_ratio = terms_matched / len(key_terms)
        
        if match_ratio >= 0.7:
            claim.support_level = ClaimSupport.FULLY_SUPPORTED
            claim.confidence = min(1.0, match_ratio)
        elif match_ratio >= 0.3:
            claim.support_level = ClaimSupport.PARTIALLY_SUPPORTED
            claim.confidence = match_ratio
        else:
            claim.support_level = ClaimSupport.UNSUPPORTED
            claim.confidence = match_ratio
        
        claim.evidence_snippets = evidence_found[:3]  # Keep top 3 evidence snippets
        
        return claim
    
    def _extract_key_terms(self, text: str) -> List[str]:
        """Extract key terms that should be verifiable in sources."""
        terms = []
        
        # Numbers and percentages
        terms.extend(re.findall(r'\d+(?:\.\d+)?%?', text))
        
        # Dollar amounts
        terms.extend(re.findall(r'\$[\d,]+(?:\.\d+)?', text))
        
        # Quoted phrases
        terms.extend(re.findall(r'"([^"]+)"', text))
        
        # Capitalized proper nouns (names, companies, etc.)
        terms.extend(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text))
        
        # Filter out common words and duplicates
        common_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been'}
        terms = [t for t in terms if t.lower() not in common_words and len(t) > 2]
        
        return list(set(terms))
    
    def verify_answer(self, answer: str, citations: List[Dict]) -> VerificationResult:
        """Verify an entire RAG answer against its citations.
        
        Args:
            answer: The generated answer text
            citations: List of citation dicts from RAG
            
        Returns:
            VerificationResult with scores and claim-level details
        """
        # Extract claims from answer
        claims = self.extract_claims(answer)
        
        if not claims:
            return VerificationResult(
                overall_score=1.0,
                claims=[],
                hallucination_risk="low",
                feedback="No specific factual claims detected in answer."
            )
        
        # Verify each claim
        verified_claims = []
        for claim in claims:
            verified = self.verify_claim(claim, citations)
            verified_claims.append(verified)
        
        # Count by support level
        fully = sum(1 for c in verified_claims if c.support_level == ClaimSupport.FULLY_SUPPORTED)
        partial = sum(1 for c in verified_claims if c.support_level == ClaimSupport.PARTIALLY_SUPPORTED)
        unsupported = sum(1 for c in verified_claims if c.support_level == ClaimSupport.UNSUPPORTED)
        no_cite = sum(1 for c in verified_claims if c.support_level == ClaimSupport.NO_CITATION)
        
        total = len(verified_claims)
        
        # Calculate overall score (CaRR-style weighted scoring)
        # Full support = 1.0, Partial = 0.5, Unsupported = 0.0, No citation = -0.2
        score = (fully * 1.0 + partial * 0.5 + unsupported * 0.0 + no_cite * -0.2) / total
        score = max(0.0, min(1.0, score))  # Clamp to [0, 1]
        
        # Determine hallucination risk
        unsupported_ratio = (unsupported + no_cite) / total
        if unsupported_ratio >= 0.5:
            risk = "high"
        elif unsupported_ratio >= 0.25:
            risk = "medium"
        else:
            risk = "low"
        
        # Generate feedback
        feedback_parts = []
        if fully > 0:
            feedback_parts.append(f"{fully} claims fully supported")
        if partial > 0:
            feedback_parts.append(f"{partial} partially supported")
        if unsupported > 0:
            feedback_parts.append(f"{unsupported} need better citations")
        if no_cite > 0:
            feedback_parts.append(f"{no_cite} missing citations")
        
        return VerificationResult(
            overall_score=score,
            claims=verified_claims,
            fully_supported_count=fully,
            partially_supported_count=partial,
            unsupported_count=unsupported,
            no_citation_count=no_cite,
            hallucination_risk=risk,
            feedback="; ".join(feedback_parts) if feedback_parts else "Answer verified"
        )
    
    def get_improvement_suggestions(self, result: VerificationResult) -> List[str]:
        """Get suggestions for improving answer quality based on verification."""
        suggestions = []
        
        if result.no_citation_count > 0:
            suggestions.append(
                f"Add citations to {result.no_citation_count} factual claims that currently lack source references."
            )
        
        if result.unsupported_count > 0:
            unsupported_claims = [c for c in result.claims if c.support_level == ClaimSupport.UNSUPPORTED]
            if unsupported_claims:
                suggestions.append(
                    f"Verify or remove {result.unsupported_count} claims that aren't supported by the cited sources."
                )
        
        if result.hallucination_risk == "high":
            suggestions.append(
                "Consider rewriting the answer to more closely follow the source material."
            )
        
        return suggestions


# Singleton instance
citation_verifier = CitationVerifier()
