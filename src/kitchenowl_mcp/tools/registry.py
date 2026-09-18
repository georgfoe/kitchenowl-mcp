from .meal_plan import add_meal_plan_entry, get_meal_plan
from .recipes import (
    audit_recipe_schema,
    create_recipe,
    delete_recipe,
    get_recipe,
    list_tags,
    mark_recipe_made,
    search_recipes,
    set_recipe_image,
    update_recipe,
)
from .shopping import (
    add_shopping_list_items,
    clear_checked_items,
    get_shopping_list,
    update_shopping_list_item,
)

ALL_TOOLS = [
    # Recipes
    search_recipes,
    get_recipe,
    create_recipe,
    update_recipe,
    set_recipe_image,
    delete_recipe,
    list_tags,
    mark_recipe_made,
    audit_recipe_schema,
    # Shopping list
    get_shopping_list,
    add_shopping_list_items,
    update_shopping_list_item,
    clear_checked_items,
    # Meal plan
    get_meal_plan,
    add_meal_plan_entry,
]
