from django.shortcuts import render


def welcome(request):
    return render(request, "core/welcome.html")


def home(request):
    return render(request, "core/home.html")
