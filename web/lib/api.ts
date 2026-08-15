export interface RecommendationItem {
  movie_id: number;
  title: string;
  genres: string[];
  tmdb_id: string | null;
  score: number;
  reason: string;
  poster_url: string | null;
  overview: string | null;
  release_year: number | null;
  metadata_source: string;
}

export interface RecommendationResponse {
  tenant_id: string;
  user_id: number;
  model_version: string;
  policy: string;
  items: RecommendationItem[];
}

export interface HistoryItem {
  movie_id: number;
  title: string;
  genres: string[];
  rating: number;
  timestamp: number;
}

export interface HistoryResponse {
  tenant_id: string;
  user_id: number;
  items: HistoryItem[];
}

export interface UserDashboard {
  recommendations: RecommendationResponse;
  history: HistoryResponse;
}

export interface PersonaItem {
  user_id: number;
  slug: string;
  display_name: string;
  description: string;
}

export interface PersonaResponse {
  tenant_id: string;
  items: PersonaItem[];
}
