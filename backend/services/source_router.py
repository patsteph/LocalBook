"""Source Type Routing Service

Routes queries to appropriate source types (tabular vs text) for better retrieval.
Tabular sources (xlsx, csv) are best for numeric/counting queries.
Text sources are best for explanatory/conceptual queries.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


class QueryIntent(Enum):
    """Types of query intent for routing."""
    NUMERIC = "numeric"       # Count, sum, average, percentage queries
    COMPARISON = "comparison"  # Compare values across entities/time
    LOOKUP = "lookup"          # Find specific data point
    EXPLANATION = "explanation"  # Why, how, explain queries
    SUMMARY = "summary"        # Overview, summarize queries
    LIST = "list"              # List items, enumerate
    MIXED = "mixed"            # Combination of intents


class SourceType(Enum):
    """Source types for routing."""
    TABULAR = "tabular"   # xlsx, csv, tables
    TEXT = "text"         # pdf, docx, txt, web
    ANY = "any"           # No preference


@dataclass
class RoutingDecision:
    """Result of routing decision."""
    primary_type: SourceType
    intent: QueryIntent
    confidence: float  # 0-1 confidence in routing decision
    boost_tabular: float  # Boost factor for tabular sources
    boost_text: float  # Boost factor for text sources
    reason: str


class SourceRouter:
    """Routes queries to appropriate source types."""
    
    def __init__(self):
        # Patterns that indicate numeric/tabular queries
        self.numeric_patterns = [
            r'\bhow many\b',
            r'\bhow much\b',
            r'\bcount\b',
            r'\btotal\b',
            r'\bsum\b',
            r'\baverage\b',
            r'\bpercentage\b',
            r'\bpercent\b',
            r'\bnumber of\b',
            r'\b\d+\s*(demos?|meetings?|calls?|sales?|deals?|leads?)\b',
            r'\bquota\b',
            r'\bmetric\b',
            r'\bkpi\b',
            r'\brevenue\b',
            r'\bpipeline\b',
            r'\bforecast\b',
        ]
        
        # Patterns that indicate comparison queries
        self.comparison_patterns = [
            r'\bcompare\b',
            r'\bversus\b',
            r'\bvs\.?\b',
            r'\bdifference between\b',
            r'\bmore than\b',
            r'\bless than\b',
            r'\bhigher\b',
            r'\blower\b',
            r'\bbetter\b',
            r'\bworse\b',
            r'\bq[1-4]\s+(?:vs|versus|compared to|and)\s+q[1-4]\b',
        ]
        
        # Patterns that indicate lookup queries
        self.lookup_patterns = [
            r'\bwhat is\b',
            r'\bwhat was\b',
            r'\bwhen did\b',
            r'\bwho did\b',
            r'\bwho has\b',
            r'\bfind\b',
            r'\blook up\b',
            r'\bget\b.*\bfor\b',
        ]
        
        # Patterns that indicate explanation queries (prefer text)
        self.explanation_patterns = [
            r'\bwhy\b',
            r'\bhow does\b',
            r'\bexplain\b',
            r'\bdescribe\b',
            r'\bwhat does.*mean\b',
            r'\bunderstand\b',
            r'\breason\b',
            r'\bcause\b',
            r'\banalysis\b',
            r'\binsight\b',
        ]
        
        # Patterns that indicate summary queries
        self.summary_patterns = [
            r'\bsummar\w+\b',
            r'\boverview\b',
            r'\bhighlights?\b',
            r'\bkey points?\b',
            r'\bmain\b.*\b(idea|point|topic)\b',
            r'\btell me about\b',
            r'\bwhat.*about\b',
        ]
        
        # Patterns that indicate list queries
        self.list_patterns = [
            r'\blist\b',
            r'\benumerate\b',
            r'\bname\s+(all|the)\b',
            r'\bwhat are (all |the )?\w+s\b',
            r'\bgive me\b.*\b(all|list)\b',
        ]
    
    def _count_pattern_matches(self, query: str, patterns: List[str]) -> int:
        """Count how many patterns match in the query."""
        query_lower = query.lower()
        matches = 0
        for pattern in patterns:
            if re.search(pattern, query_lower):
                matches += 1
        return matches
    
    def detect_intent(self, query: str) -> Tuple[QueryIntent, float]:
        """Detect the primary intent of a query.
        
        Returns: (intent, confidence)
        """
        scores = {
            QueryIntent.NUMERIC: self._count_pattern_matches(query, self.numeric_patterns),
            QueryIntent.COMPARISON: self._count_pattern_matches(query, self.comparison_patterns),
            QueryIntent.LOOKUP: self._count_pattern_matches(query, self.lookup_patterns),
            QueryIntent.EXPLANATION: self._count_pattern_matches(query, self.explanation_patterns),
            QueryIntent.SUMMARY: self._count_pattern_matches(query, self.summary_patterns),
            QueryIntent.LIST: self._count_pattern_matches(query, self.list_patterns),
        }
        
        # Find highest scoring intent
        max_score = max(scores.values())
        
        if max_score == 0:
            return QueryIntent.MIXED, 0.3
        
        # Get intent with highest score
        best_intent = max(scores, key=scores.get)
        
        # Calculate confidence based on score dominance
        total_score = sum(scores.values())
        confidence = min(0.95, 0.5 + (max_score / max(1, total_score)) * 0.5)
        
        # If multiple intents tie, return MIXED
        top_intents = [i for i, s in scores.items() if s == max_score]
        if len(top_intents) > 1:
            return QueryIntent.MIXED, confidence * 0.7
        
        return best_intent, confidence
    
    def route(self, query: str) -> RoutingDecision:
        """Route a query to appropriate source types.
        
        Returns routing decision with boost factors for each source type.
        """
        intent, confidence = self.detect_intent(query)
        
        # Default: no preference
        boost_tabular = 0.0
        boost_text = 0.0
        primary_type = SourceType.ANY
        reason = "No strong routing signal"
        
        if intent == QueryIntent.NUMERIC:
            # Strongly prefer tabular sources for numeric queries
            primary_type = SourceType.TABULAR
            boost_tabular = 0.25
            boost_text = -0.1
            reason = "Numeric query - tabular sources preferred"
            
        elif intent == QueryIntent.COMPARISON:
            # Prefer tabular for data comparisons
            primary_type = SourceType.TABULAR
            boost_tabular = 0.2
            boost_text = 0.0
            reason = "Comparison query - tabular sources preferred"
            
        elif intent == QueryIntent.LOOKUP:
            # Slight preference for tabular (structured data)
            primary_type = SourceType.TABULAR
            boost_tabular = 0.1
            boost_text = 0.05
            reason = "Lookup query - slight tabular preference"
            
        elif intent == QueryIntent.EXPLANATION:
            # Strongly prefer text sources for explanations
            primary_type = SourceType.TEXT
            boost_tabular = -0.1
            boost_text = 0.2
            reason = "Explanation query - text sources preferred"
            
        elif intent == QueryIntent.SUMMARY:
            # Prefer text sources for summaries
            primary_type = SourceType.TEXT
            boost_tabular = 0.0
            boost_text = 0.15
            reason = "Summary query - text sources preferred"
            
        elif intent == QueryIntent.LIST:
            # Both can work, slight tabular preference
            primary_type = SourceType.ANY
            boost_tabular = 0.1
            boost_text = 0.05
            reason = "List query - both source types valid"
            
        else:  # MIXED
            primary_type = SourceType.ANY
            reason = "Mixed intent - no routing preference"
        
        return RoutingDecision(
            primary_type=primary_type,
            intent=intent,
            confidence=confidence,
            boost_tabular=boost_tabular,
            boost_text=boost_text,
            reason=reason
        )
    
    def apply_routing_boost(
        self,
        results: List[Dict],
        routing: RoutingDecision
    ) -> List[Dict]:
        """Apply routing boost to search results based on source type.
        
        Modifies results in-place with routing_boost field.
        Returns results sorted by boosted score.
        """
        if routing.boost_tabular == 0 and routing.boost_text == 0:
            return results
        
        tabular_types = {'xlsx', 'csv', 'tabular', 'spreadsheet'}
        text_types = {'pdf', 'docx', 'txt', 'web', 'document', 'text', 'youtube'}
        
        for result in results:
            source_type = result.get("source_type", "").lower()
            
            # Determine boost based on source type
            if source_type in tabular_types:
                boost = routing.boost_tabular
            elif source_type in text_types:
                boost = routing.boost_text
            else:
                # Unknown type, check filename
                filename = result.get("filename", "").lower()
                if filename.endswith(('.xlsx', '.csv', '.xls')):
                    boost = routing.boost_tabular
                elif filename.endswith(('.pdf', '.docx', '.txt', '.md')):
                    boost = routing.boost_text
                else:
                    boost = 0.0
            
            result["routing_boost"] = boost
            
            # Apply to existing score
            if "boosted_score" in result:
                result["boosted_score"] += boost
            elif "rerank_score" in result:
                result["boosted_score"] = result["rerank_score"] + boost
            else:
                result["boosted_score"] = boost
        
        # Sort by boosted score
        results.sort(key=lambda r: -r.get("boosted_score", 0))
        
        boosted_count = sum(1 for r in results if r.get("routing_boost", 0) != 0)
        if boosted_count > 0:
            print(f"[SourceRouter] Applied {routing.intent.value} routing: "
                  f"{boosted_count} results adjusted ({routing.reason})")
        
        return results
    
    def get_source_type_filter(
        self,
        routing: RoutingDecision,
        available_types: List[str]
    ) -> Optional[List[str]]:
        """Get source types to filter to based on routing.
        
        Returns None if no filtering should be applied,
        or a list of source types to include.
        """
        if routing.confidence < 0.7:
            return None  # Not confident enough to filter
        
        if routing.primary_type == SourceType.ANY:
            return None
        
        tabular_types = {'xlsx', 'csv', 'tabular', 'spreadsheet'}
        text_types = {'pdf', 'docx', 'txt', 'web', 'document', 'text', 'youtube'}
        
        available_set = set(t.lower() for t in available_types)
        
        if routing.primary_type == SourceType.TABULAR:
            # Only filter if we have tabular sources
            matches = tabular_types & available_set
            if matches:
                return list(matches)
        
        elif routing.primary_type == SourceType.TEXT:
            # Only filter if we have text sources
            matches = text_types & available_set
            if matches:
                return list(matches)
        
        return None


# Singleton instance
source_router = SourceRouter()
