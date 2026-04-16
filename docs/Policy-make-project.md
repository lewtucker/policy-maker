# Policy maker

I want to create new app based on the work we did in ./OC_Policy.  Let's call it policy-maker.  policy-maker is a varient of the OC_Policy server in ./OC_Policy we've been
building but it doesn't connect to any open claw instance. 

# Purpose

It's purpose is to just allow people to explore making policies and analyzing them.

The UI would draw upon the same elements of the OC_Policy Manager Policies page.  The main policies page would show the existing policies and allow the user to add rule, delete all, edit or delete individual rules, etc. 

There would also be a rule-maker tab that would hold the UI for the policy analyst and spawn an agent in the same way as the OC_Policy.  This page would also allow the user to download or upload the skill used by the agent.  That way they could modifiy how the agent creates rules.

Policies themselves would be store in a local database under each users email address.  The login page would ask for the user's email address, and password.  For now, we can fix the password to be "ZPR".  Any user email would be accepted.  For a new user, we'd create a new entry in the database to save their rule set.  For exisiting user, we'd retrieve their rule set from the database and any changes made by the user would be stored back in the database.

This data base would also store the Skill used by the agent with each user.  Any uploaded skill would be stored back in the db.

