from __future__ import annotations

from django.urls import URLPattern, path

from tasks.views import (
    StudentTasksView,
    TaskAssigneesView,
    TaskCountView,
    TaskListView,
    TaskView,
)

urlpatterns: list[URLPattern] = [
    path("tasks/", TaskListView.as_view(), name="tasks"),
    path("tasks/count/", TaskCountView.as_view(), name="task-count"),
    path("tasks/assignees/", TaskAssigneesView.as_view(), name="task-assignees"),
    path("tasks/<int:task_id>/", TaskView.as_view(), name="task"),
    path("students/<int:student_id>/tasks/", StudentTasksView.as_view(), name="student-tasks"),
]
