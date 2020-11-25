help: ## display this help message
	@echo "Please use \`make <target>' where <target> is one of"
	@grep '^[a-zA-Z]' $(MAKEFILE_LIST) | sort | awk -F ':.*?## ' 'NF==2 {printf "\033[36m  %-25s\033[0m %s\n", $$1, $$2}'

extract_translations: ## extract localizable strings from sources
	python manage.py makemessages -l en -l ar -l he -v1 -d django

compile_messages: ## Compile .po files
	django-admin compilemessages
