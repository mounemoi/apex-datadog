.PHONY: default test coding-rule

default:
	# do nothing

lint:
	find ./ -name "*.py" | flake8 --config ./.config/flake8
