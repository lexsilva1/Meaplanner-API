# Meal Plan API Endpoints

## Create Personalized Meal Plan

**Endpoint:** `POST /api/core/meal-plans/create/`

**Authentication:** Required (Token Authentication)

**Description:** Creates a personalized meal plan for the authenticated user using AI or deterministic generation.

**Request Body:**

```json
{
  "calories": 2000,
  "goal": "maintenance",
  "model": "llama3:8b",
  "force_deterministic": false
}
```

**Parameters:**

- `calories` (integer, required): Daily calorie target (1200-5000)
- `goal` (string, optional): Fitness goal - options: "weight_loss", "muscle_gain", "maintenance" (default: "maintenance")
- `model` (string, optional): LLM model to use for AI generation (default: "llama3:8b")
- `force_deterministic` (boolean, optional): Skip AI generation and use deterministic method (default: false)

**Response (Success - 201):**

```json
{
    "success": true,
    "message": "Meal plan created successfully",
    "meal_plan": {
        "id": 1,
        "title": "AI Plan for John Doe",
        "description": "AI generated meal plan. Target: 2000 kcal/day, Goal: maintenance",
        "user_email": "john@example.com",
        "creation_time": "2025-07-08T10:30:00Z",
        "modification_time": "2025-07-08T10:30:00Z",
        "days": [
            {
                "id": 1,
                "day_type": "regular",
                "date": "2025-07-08",
                "meals": [...]
            }
        ]
    }
}
```

**Response (Error - 400):**

```json
{
  "error": "Invalid input",
  "details": {
    "calories": ["Ensure this value is greater than or equal to 1200."]
  }
}
```

## List User Meal Plans

**Endpoint:** `GET /api/core/meal-plans/`

**Authentication:** Required (Token Authentication)

**Description:** Lists all meal plans for the authenticated user.

**Response:**

```json
{
    "meal_plans": [
        {
            "id": 1,
            "title": "AI Plan for John Doe",
            "description": "AI generated meal plan...",
            "user_email": "john@example.com",
            "creation_time": "2025-07-08T10:30:00Z",
            "modification_time": "2025-07-08T10:30:00Z",
            "days": [...]
        }
    ]
}
```

## Get Meal Plan Detail

**Endpoint:** `GET /api/core/meal-plans/{meal_plan_id}/`

**Authentication:** Required (Token Authentication)

**Description:** Get detailed information about a specific meal plan.

**Response:**

```json
{
  "meal_plan": {
    "id": 1,
    "title": "AI Plan for John Doe",
    "description": "AI generated meal plan...",
    "user_email": "john@example.com",
    "creation_time": "2025-07-08T10:30:00Z",
    "modification_time": "2025-07-08T10:30:00Z",
    "days": [
      {
        "id": 1,
        "day_type": "regular",
        "date": "2025-07-08",
        "meals": [
          {
            "id": 1,
            "meal_type": "breakfast",
            "recipes": [
              {
                "id": 1,
                "meal_part_name": "main course",
                "recipe": {
                  "id": 123,
                  "title": "Oatmeal with Berries",
                  "calories": 350,
                  "protein": 12.5,
                  "carbohydrate": 45.0,
                  "fat": 8.5
                },
                "is_selected": true
              }
            ]
          }
        ]
      }
    ]
  }
}
```

## Usage Example

1. **Authenticate:** First get an authentication token
2. **Create meal plan:** POST to `/api/core/meal-plans/create/` with your parameters
3. **List meal plans:** GET `/api/core/meal-plans/` to see all your plans
4. **View details:** GET `/api/core/meal-plans/{id}/` for detailed meal information

## Notes

- The endpoint will first try to use AI generation (via the management command)
- If AI generation fails, it will fallback to deterministic generation
- The meal plan includes 3 days: regular, workout, and rest days
- Each day has different meal configurations based on the day type
- All endpoints require authentication via Token Authentication
